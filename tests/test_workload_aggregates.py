"""M3c: server-side workload aggregates over QUERY_HISTORY (spec decision 16).

The boundary these tests pin: the GROUP BY runs inside Snowflake, nothing
per-query or textual ever lands, and every report fact carries coverage.
"""

from __future__ import annotations

import re

from fixtures import REALISTIC, REALISTIC_SHOW, WORKLOAD

from md_migration_assessment.collect.manifest import EXTRACTORS, Profile, load_sql
from md_migration_assessment.handoff import _projection_columns

from conftest import FakeSource
from test_workload import collect

M3C_NAMES = {
    "query_concurrency",
    "query_tag_fingerprints",
    "client_app_fingerprints",
    "query_shapes",
    "query_workload_rollup",
    "query_dialect_constructs",
}
M3C = [e for e in EXTRACTORS if e.name in M3C_NAMES]


def _sql(name: str) -> str:
    ex = next(e for e in M3C if e.name == name)
    return load_sql("account_usage", ex.account_usage_sql)


# ── manifest boundary ───────────────────────────────────────────────────


def test_m3c_extracts_declare_their_source_view():
    assert {e.name for e in M3C} == M3C_NAMES
    for ex in M3C:
        assert ex.account_usage_view == "query_history", ex.name
        assert ex.min_profile is Profile.FULL, ex.name
        assert ex.window_from_history_days, ex.name


def test_m3c_projections_carry_no_per_query_or_identity_columns():
    forbidden = {
        "query_id", "query_text", "query_tag", "session_id", "user_name",
        "role_name", "client_ip", "start_time", "end_time",
    }
    for ex in M3C:
        cols = _projection_columns(_sql(ex.name))
        leaked = cols & forbidden
        assert not leaked, (ex.name, leaked)


def test_m3c_extracts_aggregate_server_side():
    for ex in M3C:
        assert "group by" in _sql(ex.name).lower(), ex.name


def test_query_tags_land_only_as_derived_tool_labels():
    """QUERY_TAG is free text; the raw value must never be projected —
    only the CASE-derived tool label (or 'other_tagged') lands."""
    sql = _sql("query_tag_fingerprints")
    assert "AS query_tag_tool" in sql
    assert "'other_tagged'" in sql
    # user identities land only as a distinct count, never as names
    assert re.search(r"count\(DISTINCT user_name\)", sql)
    assert not re.search(r"^\s*user_name", sql, re.MULTILINE)


def test_shapes_cap_is_explicit_never_silent():
    sql = _sql("query_shapes")
    assert "'(remainder)'" in sql
    assert "'(unhashed)'" in sql
    assert "n_shapes" in sql  # the remainder row says how many collapsed
    assert "query_text" not in sql.lower()


def test_unhashed_bucket_is_exempt_from_the_cap():
    """The '(unhashed)' bucket must survive regardless of its weight: it is
    ranked in its own partition (never consuming hashed rank slots) and the
    cap condition exempts it explicitly (review, 2026-08-19)."""
    sql = _sql("query_shapes")
    assert "shape_key = '(unhashed)' OR shape_rank" in sql
    assert re.search(
        r"PARTITION BY IFF\(COALESCE\(query_parameterized_hash", sql
    )


def test_concurrency_uses_exact_events_and_hour_boundaries():
    """Review, 2026-08-19: minute-truncated events counted sequential
    sub-minute queries as concurrent, and hours spanned by one long query
    got no rows. Events must be exact timestamps (same-instant ties netted
    by grouping, so [start, end) intervals never phantom-overlap) and hour
    boundaries must be materialized so every window hour has a row."""
    sql = _sql("query_concurrency")
    low = sql.lower()
    assert "date_trunc('minute'" not in low       # exact events, no minute grid
    assert "start_time as event_ts" in low
    assert "generator" in low                     # hour-boundary carriers
    assert "group by warehouse_name, event_ts" in low  # same-instant netting
    cols = _projection_columns(sql)
    assert {"peak_concurrent_queries", "avg_concurrent_queries",
            "busy_seconds"} <= cols


def test_dialect_scan_keeps_text_inside_aggregates():
    """query_text may appear only inside count_if(...) predicates — the
    projection itself was checked above; this pins the mechanism."""
    sql = _sql("query_dialect_constructs")
    body = "\n".join(line.split("--")[0] for line in sql.splitlines())
    for m in re.finditer(r"query_text", body, re.IGNORECASE):
        prefix = body[: m.start()].lower()
        assert prefix.rstrip().endswith("regexp_like(") or prefix.rstrip().endswith(","), (
            "query_text referenced outside an aggregate predicate"
        )


# ── collection + report facts ───────────────────────────────────────────


def test_full_profile_collects_m3c_extracts(out_db):
    coll, _ = collect(out_db)
    rows = dict(
        out_db.execute(
            "SELECT extractor, status FROM meta.extract_runs WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    )
    for name in M3C_NAMES:
        assert rows[name] == "complete", (name, rows[name])


def test_concurrency_profile_fact(out_db):
    coll, _ = collect(out_db)
    rows = out_db.execute(
        """
        SELECT warehouse_name, peak_concurrent_queries, avg_concurrent_queries,
               busy_seconds, concurrency_extract_status
        FROM report.concurrency_profile WHERE collection_id = ?
        ORDER BY warehouse_name
        """,
        [str(coll.collection_id)],
    ).fetchall()
    assert rows == [
        ("COMPUTE_WH", 2, 0.5, 660.0, "complete"),
        ("ETL_WH", 7, 2.4, 1800.0, "complete"),
    ]


def test_tool_fingerprints_union_all_three_evidence_sources(out_db):
    coll, _ = collect(out_db)
    rows = out_db.execute(
        """
        SELECT tool, detection_method, confidence, n_events, n_distinct_users
        FROM report.tool_fingerprints WHERE collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchall()
    by_key = {(r[0], r[1]): r for r in rows}
    # client_application: two days of the same driver summed, users = max (lower bound)
    assert by_key[("PythonConnector 3.12.0", "client_application")][2:] == ("high", 1000, 2)
    assert ("(unknown client)", "client_application") in by_key
    # query_tag: pattern inference is medium confidence
    assert by_key[("dbt", "query_tag")][2:] == ("medium", 500, 2)
    # login_history: connection evidence, exact distinct users
    assert by_key[("PYTHON_DRIVER", "login_history")][2:] == ("high", 48, 1)
    assert by_key[("SNOWFLAKE_UI", "login_history")][2:] == ("high", 5, 1)


def test_query_shapes_fact_keeps_cap_rollups(out_db):
    coll, _ = collect(out_db)
    rows = out_db.execute(
        """
        SELECT shape_key, n_shapes, n_queries, shapes_extract_status
        FROM report.query_shapes WHERE collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchall()
    by_key = {r[0]: r for r in rows}
    assert by_key["a1b2c3d4"][1:] == (1, 1000, "complete")
    assert by_key["(remainder)"][1:] == (57, 310, "complete")
    assert by_key["(unhashed)"][1:] == (1, 40, "complete")


def test_workload_rollup_fact(out_db):
    coll, _ = collect(out_db)
    row = out_db.execute(
        """
        SELECT warehouse_name, query_type, n_queries, n_spilled_local,
               p95_bytes_scanned, rollup_extract_status
        FROM report.workload_rollup WHERE collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchone()
    assert row == ("COMPUTE_WH", "SELECT", 1200, 14, 60000.0, "complete")


def test_dialect_constructs_fact_sums_days_and_carries_heuristic_note(out_db):
    coll, _ = collect(out_db)
    rows = out_db.execute(
        """
        SELECT construct, n_queries_matched, n_queries_scanned, source, note
        FROM report.dialect_constructs WHERE collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchall()
    by_construct = {r[0]: r for r in rows}
    assert set(by_construct) == {
        "flatten", "colon_path_variant_access", "pivot_unpivot", "connect_by",
        "match_recognize", "time_travel_at_before", "result_scan",
        "identifier_fn",
    }
    assert by_construct["flatten"][1:3] == (18, 2000)
    assert by_construct["colon_path_variant_access"][1] == 360
    assert "heuristic" in by_construct["flatten"][4]
    assert by_construct["flatten"][3] == "query_history_server_side"


def test_m3c_unavailable_extract_degrades_not_fabricates(out_db):
    from conftest import NOT_AUTHORIZED

    au = dict(REALISTIC)
    au.update(WORKLOAD)
    au["query_concurrency"] = RuntimeError(NOT_AUTHORIZED)
    source = FakeSource(
        account_usage=au, databases=["APPDB"], show_data=dict(REALISTIC_SHOW)
    )
    coll, _ = collect(out_db, source=source)
    status = out_db.execute(
        "SELECT status FROM meta.extract_runs WHERE collection_id = ? "
        "AND extractor = 'query_concurrency'",
        [str(coll.collection_id)],
    ).fetchone()[0]
    assert status == "unavailable"
    assert out_db.execute(
        "SELECT count(*) FROM report.concurrency_profile"
    ).fetchone()[0] == 0


# ── handoff boundary ────────────────────────────────────────────────────


def test_m3c_tables_travel_clean_in_handoff(out_db, tmp_path):
    from md_migration_assessment.handoff import build_handoff

    collect(out_db)
    src_path = str(tmp_path / "assessment.duckdb")  # the out_db fixture's path
    out_db.close()

    manifest = build_handoff(src_path, str(tmp_path / "handoff.duckdb"))
    for name in M3C_NAMES:
        entry = manifest["tables"][f"raw.{name}"]
        assert entry["dropped_unexpected"] == [], name
        assert entry["excluded_columns"] == [], name
        assert "query_text" not in entry.get("unclassified_included", []), name
    for fact in ("concurrency_profile", "tool_fingerprints", "query_shapes",
                 "workload_rollup", "dialect_constructs"):
        assert f"report.{fact}" in manifest["tables"]
