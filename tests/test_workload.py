"""M3b workload shape: aggregate-history extracts and their report facts.

Per-query QUERY_HISTORY is deliberately absent (spec decision 15): these
tests also pin the boundary — nothing here may collect per-query or
per-login events, and query-history-derived facts stay not_requested.
"""

from __future__ import annotations

from conftest import FakeSource
from fixtures import REALISTIC, REALISTIC_SHOW, WORKLOAD

from md_migration_assessment.collect.manifest import (
    EXTRACTORS,
    Profile,
    load_sql,
)
from md_migration_assessment.collect.runner import run_collection
from md_migration_assessment.report import build_report

WORKLOAD_EXTRACTORS = [e for e in EXTRACTORS if e.category == "workload"]
WORKLOAD_NAMES = {e.name for e in WORKLOAD_EXTRACTORS}

EXPECTED_WORKLOAD = {
    "warehouse_metering_history",
    "warehouse_load_history",
    "metering_daily_history",
    "copy_history",
    "pipe_usage_history",
    "task_history",
    "login_history",
}


def full_source() -> FakeSource:
    au = dict(REALISTIC)
    au.update(WORKLOAD)
    return FakeSource(
        account_usage=au, databases=["APPDB"], show_data=dict(REALISTIC_SHOW)
    )


def collect(out_db, profile=Profile.FULL, history_days=30, source=None):
    source = source or full_source()
    coll = run_collection(
        out_db, source, profile=profile, history_days=history_days
    )
    build_report(out_db)
    return coll, source


def extract_runs(out_db, coll):
    rows = out_db.execute(
        "SELECT extractor, status, source_used, requested_window_days "
        "FROM meta.extract_runs WHERE collection_id = ?",
        [str(coll.collection_id)],
    ).fetchall()
    return {r[0]: dict(zip(("status", "source", "window"), r[1:])) for r in rows}


# ── manifest boundary ───────────────────────────────────────────────────


def test_workload_extract_set_is_exactly_the_m3b_scope():
    assert WORKLOAD_NAMES == EXPECTED_WORKLOAD


def test_no_per_query_history_extract_exists():
    """Spec decision 15: per-query collection is M3c, off by default."""
    assert "query_history" not in {e.name for e in EXTRACTORS}
    for ex in EXTRACTORS:
        if ex.account_usage_sql:
            sql = load_sql("account_usage", ex.account_usage_sql).lower()
            assert "account_usage.query_history" not in sql, ex.name
            assert "query_attribution_history" not in sql, ex.name


def test_workload_extracts_are_full_profile_only():
    for ex in WORKLOAD_EXTRACTORS:
        assert ex.min_profile is Profile.FULL, ex.name
        assert ex.window_from_history_days, ex.name


# ── privacy by projection ───────────────────────────────────────────────


def _executable_sql(filename: str) -> str:
    """Extract SQL with comments stripped: the privacy assertions are about
    what is fetched, and a comment naming a field it refuses is not a leak."""
    raw = load_sql("account_usage", filename)
    return "\n".join(line.split("--")[0] for line in raw.splitlines()).lower()


def test_login_history_never_fetches_per_event_or_network_fields():
    sql = _executable_sql("login_history.sql")
    for forbidden in ("client_ip", "authentication_factor", "error_message",
                      "event_id", "related_event_id"):
        assert forbidden not in sql, forbidden
    # aggregated, never per-event
    assert "group by" in sql


def test_task_history_never_selects_task_sql():
    sql = _executable_sql("task_history.sql")
    assert "query_text" not in sql
    assert "condition_text" not in sql
    assert "query_id" not in sql
    assert "group by" in sql


def test_copy_history_never_retains_file_names_or_stage_paths():
    sql = _executable_sql("copy_history.sql")
    projection = sql[: sql.index("from snowflake")]
    assert "file_name" not in projection
    assert "stage_location" not in projection


def test_copy_history_separates_load_outcomes():
    """COPY_HISTORY includes failed and skipped attempts; the extract must
    keep outcomes distinguishable so they can never be counted as writes."""
    sql = _executable_sql("copy_history.sql")
    assert "load_status" in sql
    for status in ("'loaded'", "'partially loaded'", "'load failed'"):
        assert status in sql, status


def test_pipe_usage_excludes_hidden_auto_refresh_pipes():
    """NULL-name PIPE_USAGE_HISTORY rows are Snowflake's hidden auto-refresh
    pipes (external table / Iceberg metadata refresh), not Snowpipe workload."""
    sql = _executable_sql("pipe_usage_history.sql")
    assert "pipe_name is not null" in sql
    assert "pipe_id" in sql


def test_pipe_usage_classifies_named_rows_against_actual_pipes():
    """A non-NULL PIPE_NAME can be an Iceberg table with automated refresh,
    not a pipe. Rows must be classified against ACCOUNT_USAGE.PIPES and
    unmatched rows kept under a NEUTRAL category — an unmatched row may
    also be a pipe the collecting role cannot see (PIPES is filtered by
    role visibility), so it is missing evidence, never presumed Snowpipe
    and never presumed refresh (review rounds 2-3, 2026-08-19)."""
    sql = _executable_sql("pipe_usage_history.sql")
    assert "left join snowflake.account_usage.pipes" in sql
    assert "as source_kind" in sql
    assert "'snowpipe'" in sql
    assert "'unclassified'" in sql
    # the category must stay neutral: no refresh claim baked into the value
    assert "'unclassified_refresh'" not in sql


def test_pipe_usage_scope_filters_server_side(out_db):
    """PIPE_USAGE_HISTORY has no residency columns; --scope must still hold —
    a scoped run may never persist out-of-scope pipe names (P1, 2026-08-19)."""
    from md_migration_assessment.collect.runner import Scope

    source = full_source()
    run_collection(
        out_db, source, profile=Profile.FULL,
        scope=Scope.parse(["APPDB", "OTHERDB.S2"]),
    )
    rendered = [q for q in source.queries if "pipe_usage_history" in q]
    assert rendered
    assert "split_part(u.pipe_name, '.', 1) IN ('APPDB')" in rendered[0]
    assert (
        "(split_part(u.pipe_name, '.', 1) = 'OTHERDB' "
        "AND split_part(u.pipe_name, '.', 2) = 'S2')" in rendered[0]
    )


# ── collection behavior ─────────────────────────────────────────────────


def test_standard_profile_records_workload_as_not_requested(out_db):
    coll, _ = collect(out_db, profile=Profile.STANDARD)
    runs = extract_runs(out_db, coll)
    for name in WORKLOAD_NAMES:
        assert runs[name]["status"] == "not_requested", name
    # and the fact tables still exist, empty — never "relation does not exist"
    for table in ("spend_profile", "workload_profile", "ingestion_inventory"):
        assert out_db.execute(f"SELECT count(*) FROM report.{table}").fetchone()[0] == 0


def test_full_profile_collects_all_workload_extracts(out_db):
    coll, _ = collect(out_db)
    runs = extract_runs(out_db, coll)
    for name in WORKLOAD_NAMES:
        assert runs[name]["status"] == "complete", (name, runs[name])
        assert runs[name]["source"] == "account_usage", name


def test_history_days_drives_workload_windows_only(out_db):
    coll, source = collect(out_db, history_days=45)
    runs = extract_runs(out_db, coll)
    for name in WORKLOAD_NAMES:
        assert runs[name]["window"] == 45, name
    # fixed-window feature extracts are untouched by --history-days
    assert runs["search_optimization_history"]["window"] == 90
    rendered = [q for q in source.queries if "warehouse_metering_history" in q]
    assert rendered and "-45" in rendered[0]


# ── report facts ────────────────────────────────────────────────────────


def test_spend_profile_aggregates_credits_per_warehouse_day(out_db):
    coll, _ = collect(out_db)
    rows = out_db.execute(
        """
        SELECT warehouse_name, usage_date::VARCHAR, credits_used,
               metering_extract_status
        FROM report.spend_profile WHERE collection_id = ?
        ORDER BY warehouse_name, usage_date
        """,
        [str(coll.collection_id)],
    ).fetchall()
    assert rows == [
        ("COMPUTE_WH", "2026-08-11", 0.25, "complete"),
        ("ETL_WH", "2026-08-10", 2.0, "complete"),
    ]


def test_workload_profile_joins_load_and_credits_per_hour(out_db):
    coll, _ = collect(out_db)
    rows = out_db.execute(
        """
        SELECT warehouse_name, avg_running, peak_avg_running, credits_used,
               load_extract_status, metering_extract_status
        FROM report.workload_profile WHERE collection_id = ?
        ORDER BY warehouse_name
        """,
        [str(coll.collection_id)],
    ).fetchall()
    assert rows == [
        ("COMPUTE_WH", 0.5, 1.0, 0.25, "complete", "complete"),
        ("ETL_WH", 2.5, 4.0, 1.5, "complete", "complete"),
    ]


def test_ingestion_inventory_carries_provenance(out_db):
    coll, _ = collect(out_db)
    rows = out_db.execute(
        """
        SELECT table_name, load_method, days_with_writes, total_files,
               detection_method, confidence, supporting_event_count, note,
               window_start IS NOT NULL, window_end IS NOT NULL,
               last_observed_at IS NOT NULL, copy_history_extract_status
        FROM report.ingestion_inventory WHERE collection_id = ?
        ORDER BY table_name, load_method
        """,
        [str(coll.collection_id)],
    ).fetchall()
    by_key = {(r[0], r[1]): r for r in rows}
    assert set(by_key) == {("PLAIN", "snowpipe"), ("ORDERS", "copy_into")}
    plain = by_key[("PLAIN", "snowpipe")]
    assert plain[2] == 2  # days_with_writes
    assert plain[3] == 48  # total_files
    assert plain[4] == "copy_history"
    assert plain[5] == "high"
    assert plain[6] == 48
    assert "M3c" in plain[7]  # note names the missing MERGE/INSERT evidence
    assert plain[8] and plain[9] and plain[10]
    assert plain[11] == "complete"


def test_failed_copy_attempts_are_evidence_not_writes(out_db):
    """A table whose only load attempts failed must not appear as written,
    and failed files must not inflate a written table's counts — but the
    failed rows stay in raw.copy_history as evidence."""
    coll, _ = collect(out_db)
    tables = {
        r[0]
        for r in out_db.execute(
            "SELECT table_name FROM report.ingestion_inventory "
            "WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    }
    assert "BROKEN_T" not in tables
    raw_failed = out_db.execute(
        "SELECT count(*) FROM raw.copy_history WHERE load_status = 'failed'"
    ).fetchone()[0]
    assert raw_failed == 2  # PLAIN's failed day + BROKEN_T


def test_ingestion_inventory_has_no_stale_labels():
    """Stale-candidate labeling needs all write methods observed (M3c);
    copy evidence alone must not imply a table is unwritten."""
    from md_migration_assessment.report import _INGESTION_DDL

    assert "stale" not in _INGESTION_DDL.lower()


def test_workload_profile_without_metering_marks_credits_unknown(out_db):
    from conftest import NOT_AUTHORIZED

    au = dict(REALISTIC)
    au.update(WORKLOAD)
    au["warehouse_metering_history"] = RuntimeError(NOT_AUTHORIZED)
    source = FakeSource(
        account_usage=au, databases=["APPDB"], show_data=dict(REALISTIC_SHOW)
    )
    coll, _ = collect(out_db, source=source)
    rows = out_db.execute(
        "SELECT credits_used, metering_extract_status FROM report.workload_profile "
        "WHERE collection_id = ?",
        [str(coll.collection_id)],
    ).fetchall()
    assert rows
    for credits, status in rows:
        assert credits is None
        assert status == "unavailable"
    # and spend_profile is empty rather than fabricated
    assert out_db.execute("SELECT count(*) FROM report.spend_profile").fetchone()[0] == 0


# ── handoff boundary ────────────────────────────────────────────────────


def test_workload_tables_travel_sanitized_in_handoff(out_db, tmp_path):
    from md_migration_assessment.handoff import build_handoff

    collect(out_db)
    src_path = str(tmp_path / "assessment.duckdb")  # the out_db fixture's path
    out_db.close()

    dest = str(tmp_path / "handoff.duckdb")
    manifest = build_handoff(src_path, dest)

    for name in WORKLOAD_NAMES:
        entry = manifest["tables"][f"raw.{name}"]
        assert entry["dropped_unexpected"] == [], name
        assert entry["excluded_columns"] == [], name  # aggregates carry no bodies/text
    login = manifest["tables"]["raw.login_history"]
    assert login["sensitive_included"] == {"user_identity": ["user_name"]}
    assert "report.spend_profile" in manifest["tables"]
    assert "report.workload_profile" in manifest["tables"]
    assert "report.ingestion_inventory" in manifest["tables"]
