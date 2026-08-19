"""Live integration tests against a real Snowflake account.

Skipped unless SNOWFLAKE_ACCOUNT is set. Run after tests/integration/seed.py:

    set -a; source .env.snowflake; set +a
    .venv/bin/pytest tests/integration -v

test_lite_profile works immediately after seeding (INFORMATION_SCHEMA is
real-time). test_standard_profile needs ACCOUNT_USAGE to catch up
(~45min-2h after object creation); until then it may see zero rows, which is
fine — what it must NOT see is a 'failed' status, since those indicate real
defects (column drift, type-mapping errors) rather than latency.
"""

from __future__ import annotations

import os

import pytest

from md_migration_assessment.collect.manifest import EXTRACTORS, Profile
from md_migration_assessment.collect.runner import Scope, run_collection
from md_migration_assessment.db import open_output

pytestmark = pytest.mark.skipif(
    not os.environ.get("SNOWFLAKE_ACCOUNT"),
    reason="live Snowflake credentials not configured",
)


@pytest.fixture(scope="module")
def source():
    from md_migration_assessment.collect.snowflake import SnowflakeConfig, SnowflakeSource

    src = SnowflakeSource.open(SnowflakeConfig.from_env())
    yield src
    src.close()


def _runs(con, coll):
    rows = con.execute(
        """
        SELECT extractor, status, source_used, rows_written, error_detail
        FROM meta.extract_runs WHERE collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchall()
    return {r[0]: dict(zip(("status", "source", "rows", "error"), r[1:])) for r in rows}


def _print_report(runs):
    for name, r in sorted(runs.items()):
        print(f"  {name:<32} {r['status']:<13} {str(r['source']):<20} {r['rows']} rows")
        if r["error"] and r["status"] not in ("complete", "not_requested"):
            print(f"    -> {r['error'][:160]}")


def test_lite_profile(tmp_path, source):
    con = open_output(str(tmp_path / "lite.duckdb"))
    coll = run_collection(con, source, profile=Profile.LITE)
    runs = _runs(con, coll)
    _print_report(runs)

    failed = {n: r for n, r in runs.items() if r["status"] == "failed"}
    assert not failed, f"failed extractors: {failed}"

    # catalog extracts must be complete via INFORMATION_SCHEMA with real rows
    for name in ("databases", "schemata", "tables", "columns", "views", "functions"):
        assert runs[name]["status"] == "complete", (name, runs[name])
        assert runs[name]["source"] == "information_schema"
        assert runs[name]["rows"] > 0, f"{name}: expected seeded rows immediately"

    # seeded objects are visible
    n = con.execute(
        "SELECT count(*) FROM raw.tables WHERE table_catalog = 'MDA_TEST_MAIN'"
    ).fetchone()[0]
    assert n >= 3
    # M3a seed fixtures with lag-free INFORMATION_SCHEMA visibility: assert
    # the exact seeded objects, so pre-existing unrelated objects cannot mask
    # a broken seed statement (review 2026-08-18)
    n_vec = con.execute(
        "SELECT count(*) FROM raw.columns WHERE table_catalog = 'MDA_TEST_MAIN' "
        "AND table_schema = 'ANALYTICS' AND table_name = 'EMBEDDINGS' "
        "AND column_name = 'EMB' AND data_type LIKE 'VECTOR%'"
    ).fetchone()[0]
    assert n_vec == 1, "seeded EMBEDDINGS.EMB VECTOR column missing"
    assert runs["external_tables"]["status"] == "complete"
    n_ext = con.execute(
        "SELECT count(*) FROM raw.external_tables WHERE table_catalog = 'MDA_TEST_MAIN' "
        "AND table_schema = 'ANALYTICS' AND table_name = 'EXT_TIPS'"
    ).fetchone()[0]
    assert n_ext == 1, "seeded EXT_TIPS external table missing"
    # sensitive source bodies landed (needed for dialect assessment)
    vd = con.execute(
        "SELECT view_definition FROM raw.views "
        "WHERE table_name = 'ORDER_SUMMARY' AND view_definition IS NOT NULL"
    ).fetchall()
    assert vd, "expected a non-null view definition for owned view"
    con.close()


def test_standard_profile(tmp_path, source):
    from md_migration_assessment.report import build_report
    from md_migration_assessment.report.signals import PLANNED_SIGNALS, SIGNALS

    con = open_output(str(tmp_path / "standard.duckdb"))
    coll = run_collection(con, source, profile=Profile.STANDARD)
    runs = _runs(con, coll)
    _print_report(runs)

    # 'failed' = real defect (column drift, SQL error). Latency shows up as
    # complete-with-zero-rows, which is acceptable here.
    failed = {n: r for n, r in runs.items() if r["status"] == "failed"}
    assert not failed, f"failed extractors: {failed}"

    for ex in EXTRACTORS:
        # decision 17: standard requests every extractor
        assert runs[ex.name]["status"] in ("complete", "partial", "unavailable"), (
            ex.name,
            runs[ex.name],
        )

    # feature inventory: every signal gets a row and no probe may crash on
    # real data (a probe failure = column-shape drift between probe and raw)
    build_report(con)
    feats = con.execute(
        """
        SELECT feature, observation_status, count, note
        FROM report.feature_inventory WHERE collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchall()
    assert len(feats) == len(SIGNALS) + len(PLANNED_SIGNALS)
    broken = [f for f in feats if f[3] and f[3].startswith("probe failed")]
    assert not broken, f"probes broken on real data: {broken}"
    observed = [f"  {f[0]}={f[2]}" for f in feats if f[1] == "observed"]
    print("features observed live:")
    print("\n".join(observed) or "  (none yet — ACCOUNT_USAGE lag)")

    # Deterministic fixtures on the seeded Enterprise trial account: these
    # signals are lag-free (SHOW / INFORMATION_SCHEMA) or long-settled in
    # ACCOUNT_USAGE. A broken seed statement must fail this test.
    observed_names = {f[0] for f in feats if f[1] == "observed"}
    expected = {
        "streams", "warehouses", "multi_cluster_warehouses", "streamlit_apps",
        "external_tables", "snowpipes", "scheduled_tasks", "dynamic_tables",
        "transient_tables", "secure_views",
    }
    missing = expected - observed_names
    assert not missing, f"expected seeded signals not observed: {missing}"

    # ...and the named raw fixtures behind them, so unrelated account objects
    # cannot mask a broken seed statement
    named_fixtures = [
        ("raw.streams", "name = 'ORDERS_STREAM' AND database_name = 'MDA_TEST_MAIN' "
         "AND schema_name = 'SALES'"),
        ("raw.warehouses", "name = 'MDA_MULTI_WH' AND "
         "coalesce(try_cast(max_cluster_count::VARCHAR AS INTEGER), 1) >= 2"),
        ("raw.streamlit_apps", "name = 'SALES_APP' AND database_name = 'MDA_TEST_MAIN' "
         "AND schema_name = 'ANALYTICS'"),
        ("raw.external_tables", "table_name = 'EXT_TIPS' AND "
         "table_catalog = 'MDA_TEST_MAIN' AND table_schema = 'ANALYTICS'"),
        ("raw.pipes", "pipe_name = 'LOAD_PIPE' AND pipe_catalog = 'MDA_TEST_MAIN' "
         "AND pipe_schema = 'SALES'"),
        ("raw.tasks", "task_name = 'DAILY_ROLLUP' AND task_database = 'MDA_TEST_MAIN' "
         "AND task_schema = 'SALES'"),
        ("raw.tables", "table_name = 'ORDERS_DYNAMIC' AND "
         "table_catalog = 'MDA_TEST_MAIN' AND table_schema = 'ANALYTICS' AND "
         "upper(coalesce(is_dynamic::VARCHAR, 'NO')) IN ('YES', 'TRUE', 'Y')"),
    ]
    for table, predicate in named_fixtures:
        n = con.execute(f"SELECT count(*) FROM {table} WHERE {predicate}").fetchone()[0]
        assert n >= 1, f"named seed fixture missing: {table} WHERE {predicate}"
    con.close()


def test_workload_extracts_live(tmp_path, source):
    """M3b: workload extracts must run clean against real ACCOUNT_USAGE.

    Row counts are activity-dependent (metering lags a few hours), so this
    asserts statuses and shape, never volume — a zero-row complete extract
    is latency, a 'failed' one is column drift or a broken aggregate.
    """
    from md_migration_assessment.report import build_report

    con = open_output(str(tmp_path / "workload.duckdb"))
    coll = run_collection(con, source, profile=Profile.STANDARD, history_days=30)
    runs = _runs(con, coll)
    _print_report(runs)

    failed = {n: r for n, r in runs.items() if r["status"] == "failed"}
    assert not failed, f"failed extractors: {failed}"

    workload = [ex for ex in EXTRACTORS if ex.category == "workload"]
    for ex in workload:
        assert runs[ex.name]["status"] in ("complete", "partial", "unavailable"), (
            ex.name,
            runs[ex.name],
        )

    # fact tables must build over whatever landed, and login evidence must
    # be aggregate-shaped (no per-event columns can exist in the raw table)
    build_report(con)
    for table in ("spend_profile", "workload_profile", "ingestion_inventory",
                  "concurrency_profile", "tool_fingerprints", "query_shapes",
                  "workload_rollup", "dialect_constructs"):
        n = con.execute(f"SELECT count(*) FROM report.{table}").fetchone()[0]
        print(f"  report.{table}: {n} rows")
    if runs["login_history"]["status"] == "complete" and runs["login_history"]["rows"]:
        cols = {
            r[0].lower()
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'raw' AND table_name = 'login_history'"
            ).fetchall()
        }
        assert "client_ip" not in cols and "event_id" not in cols
        assert "n_logins" in cols
    con.close()


def test_standard_profile_scoped(tmp_path, source):
    con = open_output(str(tmp_path / "scoped.duckdb"))
    scope = Scope.parse(["MDA_TEST_MAIN"])
    coll = run_collection(con, source, profile=Profile.STANDARD, scope=scope)
    runs = _runs(con, coll)

    failed = {n: r for n, r in runs.items() if r["status"] == "failed"}
    assert not failed, f"failed extractors: {failed}"

    # nothing outside the scoped database may land
    if runs["tables"]["rows"] > 0:
        other = con.execute(
            "SELECT count(*) FROM raw.tables WHERE table_catalog <> 'MDA_TEST_MAIN'"
        ).fetchone()[0]
        assert other == 0
    con.close()
