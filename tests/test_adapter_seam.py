"""The source-adapter seam, exercised end to end with a synthetic adapter.

Nothing here knows about Snowflake. If these pass, the neutral core —
runner, resume, meta schema, report observation contract, handoff column
policy, registry — works for an adapter it has never seen.
"""

from __future__ import annotations

import json
from datetime import timedelta

import duckdb
import pyarrow as pa
import pytest

from fake_adapter import (
    ADAPTER,
    DENIED,
    SERVER_ANCHOR,
    FakeConnection,
    things_table,
)

from md_migration_assessment.collect.extractor import (
    Command,
    Extractor,
    GlobalQuery,
    Profile,
)
from md_migration_assessment.collect.runner import Scope, run_collection
from md_migration_assessment.db import open_output
from md_migration_assessment.handoff import build_handoff
from md_migration_assessment.report import build_report
from md_migration_assessment.sources import get_adapter, register, unregister
from md_migration_assessment.sources.base import Connection, SourceAdapter


@pytest.fixture(autouse=True)
def registered_fake():
    register(ADAPTER)
    yield
    unregister(ADAPTER.name)


def runs(con, coll):
    cols = ["status", "source_used", "actual_scope", "rows_written", "error_detail",
            "error_category", "retryable", "actual_window_start", "actual_window_end"]
    out = {}
    for row in con.execute(
        f"SELECT extractor, {', '.join(cols)} FROM meta.extract_runs "
        "WHERE collection_id = ?", [str(coll.collection_id)],
    ).fetchall():
        out[row[0]] = dict(zip(cols, row[1:]))
    return out


# ── contract shape ───────────────────────────────────────────────────────


def test_fake_adapter_satisfies_the_protocols():
    assert isinstance(ADAPTER, SourceAdapter)
    assert isinstance(FakeConnection(), Connection)


def test_registry_resolves_registered_and_builtin_and_rejects_unknown():
    assert get_adapter("fakewh") is ADAPTER
    assert get_adapter("FAKEWH") is ADAPTER
    assert get_adapter("snowflake").name == "snowflake"
    with pytest.raises(ValueError, match="unknown source 'teradata'"):
        get_adapter("teradata")


def test_extractor_model_rejects_ambiguous_strategies():
    with pytest.raises(ValueError, match="no source strategy"):
        Extractor(name="x", category="catalog", min_profile=Profile.LITE, sources=())
    with pytest.raises(ValueError, match="exclusive"):
        Extractor(
            name="x", category="catalog", min_profile=Profile.LITE,
            sources=(Command("LIST X", ("a",), "list"), GlobalQuery("g.sql", "global")),
        )


# ── collection ───────────────────────────────────────────────────────────


def test_standard_collection_records_source_identity(out_db):
    conn = FakeConnection()
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD)
    row = out_db.execute(
        "SELECT source_kind, source_deployment, source_version, source_region, "
        "raw_schema_version, finished_at FROM meta.collections"
    ).fetchone()
    assert row[:5] == ("fakewh", "fake-proj-1", "0.1-fake", "moon-1", 1)
    assert row[5] is not None
    st = runs(out_db, coll)
    assert {r["status"] for r in st.values()} == {"complete"}
    assert st["things"]["source_used"] == "global"
    assert st["gizmos"]["source_used"] == "list"
    # the windowed extract anchored to the fake server clock, lag disclosed
    assert st["events"]["actual_window_end"] == SERVER_ANCHOR - timedelta(minutes=10)
    assert st["events"]["actual_window_start"] == SERVER_ANCHOR - timedelta(days=30)


def test_lite_skips_profile_gated_strategy_and_uses_per_database_walk(out_db):
    conn = FakeConnection()
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    st = runs(out_db, coll)
    assert st["things"]["status"] == "complete"
    assert st["things"]["source_used"] == "per_db"
    assert json.loads(st["things"]["actual_scope"]) == ["appdb", "otherdb"]
    assert st["widget_sizes"]["status"] == "not_requested"
    assert st["events"]["status"] == "not_requested"
    assert not any("sys.all_things" in q for q in conn.queries)
    # identifiers were quoted with the adapter's grammar, not Snowflake's
    assert any("`appdb`.catalog.things" in q for q in conn.queries)


def test_unavailable_global_falls_through_to_per_database(out_db):
    conn = FakeConnection(global_data={"things": Exception(DENIED)})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD)
    row = runs(out_db, coll)["things"]
    assert row["status"] == "complete"
    assert row["source_used"] == "per_db"
    assert "global not accessible" in row["error_detail"]
    assert "fallback" in row["error_detail"]


def test_real_failure_does_not_fall_through(out_db):
    conn = FakeConnection(global_data={"things": Exception("disk on fire")})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD)
    row = runs(out_db, coll)["things"]
    assert row["status"] == "failed"
    assert row["retryable"] is True
    assert not any(".catalog.things" in q for q in conn.queries)


def test_unavailable_with_no_fallback_is_unavailable_and_creates_no_raw_table(out_db):
    conn = FakeConnection(global_data={"widget_sizes": Exception(DENIED)})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD)
    row = runs(out_db, coll)["widget_sizes"]
    assert row["status"] == "unavailable"
    assert row["error_category"] == "privilege"
    assert "no fallback" in row["error_detail"]
    assert not out_db.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='raw' AND table_name='widget_sizes'"
    ).fetchone()[0]


def test_partial_per_database_walk_records_coverage(out_db):
    conn = FakeConnection(per_db_data={"things": {"otherdb": Exception(DENIED)}})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    row = runs(out_db, coll)["things"]
    assert row["status"] == "partial"
    assert json.loads(row["actual_scope"]) == ["appdb"]
    assert row["error_category"] == "privilege"


def test_truncated_command_is_partial(out_db):
    conn = FakeConnection(command_data={"gizmos": (FakeConnection().command_data["gizmos"], True)})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    row = runs(out_db, coll)["gizmos"]
    assert row["status"] == "partial"
    assert "truncated" in row["error_detail"]


def test_scope_uses_the_adapter_grammar(out_db):
    # the fake warehouse is lower-case; Snowflake-style upper-casing would
    # break both the predicate and the per-database filter
    scope = Scope.parse(["APPDB.S1"], ADAPTER.scope)
    assert scope.schemas == frozenset({("appdb", "s1")})
    with pytest.raises(ValueError, match="PROJECT.DATASET"):
        Scope.parse(["bad-name"], ADAPTER.scope)

    conn = FakeConnection()
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD, scope=scope)
    global_q = next(q for q in conn.queries if "sys.all_things" in q)
    assert "(db_name = 'appdb' AND schema_name = 's1')" in global_q
    # command output was filtered client-side with the same grammar
    names = {r[0] for r in out_db.execute("SELECT name FROM raw.gizmos").fetchall()}
    assert names == {"g_mint", "g_global"}  # s2 dropped, deployment-level kept
    st = runs(out_db, coll)
    assert st["gizmos"]["actual_scope"] == '["appdb.s1"]'


# ── interruption and resume ──────────────────────────────────────────────


def test_interrupt_and_resume_round_trip(out_db):
    conn = FakeConnection(global_data={"widget_sizes": KeyboardInterrupt()})
    with pytest.raises(KeyboardInterrupt):
        run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD)
    st = dict(out_db.execute("SELECT extractor, status FROM meta.extract_runs").fetchall())
    assert st == {
        "things": "complete", "widget_sizes": "interrupted",
        "gizmos": "interrupted", "events": "interrupted",
    }
    assert out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0] is None

    conn2 = FakeConnection()
    coll = run_collection(out_db, ADAPTER, conn2, profile=Profile.LITE, resume=True)
    st = runs(out_db, coll)
    assert {r["status"] for r in st.values()} == {"complete"}
    assert not any("sys.all_things" in q for q in conn2.queries)  # things kept
    assert any("sys.widget_sizes" in q for q in conn2.queries)     # stored STANDARD won


def test_resume_refuses_a_different_deployment_or_source_kind(out_db):
    run_collection(out_db, ADAPTER, FakeConnection(), profile=Profile.LITE)
    out_db.execute("UPDATE meta.extract_runs SET status = 'failed' WHERE extractor = 'things'")
    with pytest.raises(ValueError, match="fake-proj-2"):
        run_collection(
            out_db, ADAPTER, FakeConnection(deployment="fake-proj-2"),
            profile=Profile.LITE, resume=True,
        )
    snowflake = get_adapter("snowflake")
    with pytest.raises(ValueError, match="source 'fakewh'"):
        run_collection(out_db, snowflake, FakeConnection(), profile=Profile.LITE, resume=True)


# ── report ───────────────────────────────────────────────────────────────


def test_report_dispatches_on_source_kind(out_db):
    coll = run_collection(out_db, ADAPTER, FakeConnection(), profile=Profile.STANDARD)
    summary = build_report(out_db)
    feats = {
        r[0]: r[1:] for r in out_db.execute(
            "SELECT feature, observation_status, count, sample_objects, note "
            "FROM report.feature_inventory WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    }
    assert set(feats) == {"spicy_things", "mint_gizmos", "unicorns"}
    # the system-furniture exclusion came from the adapter's probe
    assert feats["spicy_things"][:2] == ("observed", 1)
    assert feats["spicy_things"][2] == ["appdb.s1.hot"]
    assert feats["mint_gizmos"][:2] == ("observed", 2)
    assert feats["unicorns"][0] == "unknown"
    assert "not implemented" in feats["unicorns"][3]
    assert summary == {"collections": 1, "features": 3, "unknown": 1}
    # the adapter's fact builder ran after the inventory
    totals = out_db.execute(
        "SELECT db_name, total_bytes, sizes_extract_status FROM report.widget_totals"
    ).fetchall()
    assert totals == [("appdb", 350, "complete")]


def test_report_fact_relation_exists_when_evidence_is_missing(out_db):
    run_collection(out_db, ADAPTER, FakeConnection(), profile=Profile.LITE)
    build_report(out_db)
    assert out_db.execute("SELECT count(*) FROM report.widget_totals").fetchone()[0] == 0


def test_report_refuses_raw_schema_skew_per_adapter(out_db):
    run_collection(out_db, ADAPTER, FakeConnection(), profile=Profile.LITE)
    out_db.execute("UPDATE meta.collections SET raw_schema_version = 99")
    with pytest.raises(ValueError, match="fakewh reports for v1"):
        build_report(out_db)


def test_report_refuses_unknown_source_kind(out_db):
    run_collection(out_db, ADAPTER, FakeConnection(), profile=Profile.LITE)
    out_db.execute("UPDATE meta.collections SET source_kind = 'teradata'")
    with pytest.raises(ValueError, match="unknown source 'teradata'"):
        build_report(out_db)


# ── handoff ──────────────────────────────────────────────────────────────


def test_handoff_applies_column_policy_through_the_adapter(tmp_path):
    src = str(tmp_path / "private.duckdb")
    con = open_output(src)
    run_collection(con, ADAPTER, FakeConnection(), profile=Profile.STANDARD)
    build_report(con)
    # a drifted column the fake extract SQL never produced
    con.execute("ALTER TABLE raw.things ADD COLUMN injected VARCHAR")
    con.close()

    dest = str(tmp_path / "handoff.duckdb")
    manifest = build_handoff(src, dest)
    things = manifest["tables"]["raw.things"]
    assert things["excluded_columns"] == ["body"]            # SOURCE_BODY stripped
    assert things["dropped_unexpected"] == ["injected"]      # drift dropped
    assert "thing_name" in things["sensitive_included"]["object_name"]
    assert "owner" in things["sensitive_included"]["user_identity"]
    assert things["unclassified_included"] == ["thing_kind"]
    # command allowlist honored
    assert manifest["tables"]["raw.gizmos"]["dropped_unexpected"] == []
    out = duckdb.connect(dest, read_only=True)
    try:
        cols = {r[0] for r in out.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='raw' AND table_name='things'").fetchall()}
        assert "body" not in cols and "injected" not in cols
        assert out.execute("SELECT count(*) FROM report.widget_totals").fetchone()[0] == 1
    finally:
        out.close()


def test_handoff_refuses_raw_schema_skew_per_adapter(tmp_path):
    src = str(tmp_path / "private.duckdb")
    con = open_output(src)
    run_collection(con, ADAPTER, FakeConnection(), profile=Profile.LITE)
    con.execute("UPDATE meta.collections SET raw_schema_version = 7")
    con.close()
    with pytest.raises(ValueError, match="fakewh handoffs for v1"):
        build_handoff(src, str(tmp_path / "h.duckdb"))


def test_empty_result_is_observed_zero(out_db):
    empty = things_table([])
    coll = run_collection(
        out_db, ADAPTER, FakeConnection(global_data={"things": empty}),
        profile=Profile.STANDARD,
    )
    assert runs(out_db, coll)["things"]["rows_written"] == 0
    build_report(out_db)
    status = out_db.execute(
        "SELECT observation_status FROM report.feature_inventory WHERE feature = 'spicy_things'"
    ).fetchone()[0]
    assert status == "observed_zero"


# ── review round 1 (PR #2) ───────────────────────────────────────────────


def test_wholly_unavailable_per_database_strategy_falls_through(out_db):
    """P1: the ordered-strategy contract must hold when a PerDatabaseQuery
    comes first and every database is a privilege gap — the next strategy
    still gets its turn."""
    import dataclasses

    from fake_adapter import EXTRACTORS, PerDatabaseQuery

    things = next(e for e in EXTRACTORS if e.name == "things")
    reversed_things = dataclasses.replace(
        things,
        sources=(
            PerDatabaseQuery("per_db/things.sql", "per_db"),
            GlobalQuery("global/things.sql", "global", min_profile=Profile.STANDARD),
        ),
    )
    adapter = dataclasses.replace(ADAPTER, extractors=[reversed_things])
    conn = FakeConnection(
        per_db_data={"things": {"appdb": Exception(DENIED), "otherdb": Exception(DENIED)}}
    )
    coll = run_collection(out_db, adapter, conn, profile=Profile.STANDARD)
    row = runs(out_db, coll)["things"]
    assert row["status"] == "complete"
    assert row["source_used"] == "global"
    assert row["rows_written"] == 3
    assert "per_db not accessible" in row["error_detail"]
    assert any("sys.all_things" in q for q in conn.queries)

    # ...and when nothing remains after the fall-through, it is unavailable
    # with the per-database failures disclosed (not a real failure)
    out_db.execute("DELETE FROM meta.collections")
    out_db.execute("DELETE FROM meta.extract_runs")
    coll = run_collection(out_db, adapter, conn, profile=Profile.LITE)  # global gated
    row = runs(out_db, coll)["things"]
    assert row["status"] == "unavailable"
    assert row["error_category"] == "privilege"
    assert row["source_used"] is None
    assert "per_db: no database could be read" in row["error_detail"]


def test_resume_validates_stored_collection_before_using_the_connection(out_db):
    """P1: a resume must not talk to the warehouse until the stored
    collection has been checked against the adapter in use."""
    run_collection(out_db, ADAPTER, FakeConnection(), profile=Profile.LITE)
    out_db.execute("UPDATE meta.extract_runs SET status = 'failed' WHERE extractor = 'things'")

    class Untouchable(FakeConnection):
        def session_info(self):
            raise AssertionError("connection used before the stored collection was validated")

    snowflake = get_adapter("snowflake")
    with pytest.raises(ValueError, match="source 'fakewh'"):
        run_collection(out_db, snowflake, Untouchable(), profile=Profile.LITE, resume=True)


def test_cli_resume_resolves_the_stored_adapter_locally(tmp_path):
    """P1: `md-assess collect --resume` on a non-default source's database
    must open THAT source, never the default Snowflake connection."""
    from typer.testing import CliRunner

    from md_migration_assessment.cli import app

    path = str(tmp_path / "fake.duckdb")
    con = open_output(path)
    run_collection(con, ADAPTER, FakeConnection(), profile=Profile.LITE)
    con.execute("UPDATE meta.extract_runs SET status = 'failed' WHERE extractor = 'things'")
    con.close()

    runner = CliRunner()
    # default --source omitted: the stored 'fakewh' adapter is opened (its
    # open() deliberately raises, proving Snowflake was never attempted)
    result = runner.invoke(app, ["collect", "--output", path, "--resume"])
    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "fake adapter has no environment" in str(result.exception)

    # an explicit conflicting --source is a usage error, rejected locally
    result = runner.invoke(app, ["collect", "--output", path, "--resume", "--source", "snowflake"])
    assert result.exit_code == 2
    assert "conflicts with the existing collection" in result.output
    assert "Traceback" not in result.output


def test_pre_v3_meta_schema_is_a_clear_recollect_error(tmp_path):
    """P2: META_SCHEMA_VERSION is validated before any v3-only column is
    read — a v2 file gets the re-collect guidance, not a binder error."""
    path = str(tmp_path / "old.duckdb")
    con = open_output(path)
    run_collection(con, ADAPTER, FakeConnection(), profile=Profile.LITE)
    # shape a v2 file: no source_kind column, stamped v2
    con.execute("ALTER TABLE meta.collections DROP COLUMN source_kind")
    con.execute("UPDATE meta.collections SET meta_schema_version = 2")
    with pytest.raises(ValueError, match="meta schema v2.*[Rr]e-collect"):
        build_report(con)
    with pytest.raises(ValueError, match="meta schema v2"):
        run_collection(con, ADAPTER, FakeConnection(), profile=Profile.LITE, resume=True)
    con.close()
    with pytest.raises(ValueError, match="meta schema v2"):
        build_handoff(path, str(tmp_path / "h.duckdb"))

    from typer.testing import CliRunner

    from md_migration_assessment.cli import app

    result = CliRunner().invoke(app, ["report", "--db", path])
    assert result.exit_code == 1
    assert "meta schema v2" in result.output
    assert "BinderException" not in result.output


def test_report_rebuild_removes_obsolete_report_relations(out_db):
    """P2: report.* is tool-owned; a relation from an older adapter version
    must not survive a rebuild (handoff would ship it)."""
    run_collection(out_db, ADAPTER, FakeConnection(), profile=Profile.STANDARD)
    build_report(out_db)
    out_db.execute("CREATE TABLE report.stale_facts AS SELECT 1 AS leftover")
    out_db.execute("CREATE VIEW report.stale_view AS SELECT 1 AS leftover")
    build_report(out_db)
    relations = {
        r[0] for r in out_db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'report'"
        ).fetchall()
    }
    assert "stale_facts" not in relations and "stale_view" not in relations
    assert {"feature_inventory", "widget_totals"} <= relations


def test_cli_rejects_unknown_source_as_usage_error(tmp_path):
    """P3: a bad --source is a usage error with the supported list, not a
    Python traceback."""
    from typer.testing import CliRunner

    from md_migration_assessment.cli import app

    result = CliRunner().invoke(
        app, ["collect", "--source", "teradata", "--output", str(tmp_path / "x.duckdb")]
    )
    assert result.exit_code == 2
    assert "unknown source 'teradata'" in result.output
    assert "snowflake" in result.output
    assert "Traceback" not in result.output and "ValueError" not in result.output


def _per_db_first_adapter():
    import dataclasses

    from fake_adapter import EXTRACTORS, PerDatabaseQuery

    things = next(e for e in EXTRACTORS if e.name == "things")
    reversed_things = dataclasses.replace(
        things,
        sources=(
            PerDatabaseQuery("per_db/things.sql", "per_db"),
            GlobalQuery("global/things.sql", "global", min_profile=Profile.STANDARD),
        ),
    )
    return dataclasses.replace(ADAPTER, extractors=[reversed_things])


def test_unavailable_database_enumeration_falls_through(out_db):
    """Review round 2 (P1): a privilege-classified failure to enumerate
    databases is an unavailable PerDatabaseQuery strategy, not a final
    failure — the next declared strategy still runs."""
    adapter = _per_db_first_adapter()
    conn = FakeConnection()
    conn.enumeration_error = Exception(DENIED)
    coll = run_collection(out_db, adapter, conn, profile=Profile.STANDARD)
    row = runs(out_db, coll)["things"]
    assert row["status"] == "complete"
    assert row["source_used"] == "global"
    assert "per_db not accessible (could not enumerate databases" in row["error_detail"]
    assert any("sys.all_things" in q for q in conn.queries)


def test_unavailable_database_enumeration_with_no_remaining_strategy(out_db):
    adapter = _per_db_first_adapter()
    conn = FakeConnection()
    conn.enumeration_error = Exception(DENIED)
    coll = run_collection(out_db, adapter, conn, profile=Profile.LITE)  # global gated
    row = runs(out_db, coll)["things"]
    assert row["status"] == "unavailable"
    assert row["error_category"] == "privilege"
    assert row["source_used"] is None
    assert "could not enumerate databases" in row["error_detail"]


def test_real_database_enumeration_error_is_still_failed(out_db):
    adapter = _per_db_first_adapter()
    conn = FakeConnection()
    conn.enumeration_error = Exception("network partition")
    coll = run_collection(out_db, adapter, conn, profile=Profile.STANDARD)
    row = runs(out_db, coll)["things"]
    assert row["status"] == "failed"
    assert row["retryable"] is True
    assert not any("sys.all_things" in q for q in conn.queries)
