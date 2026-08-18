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
    # sensitive source bodies landed (needed for dialect assessment)
    vd = con.execute(
        "SELECT view_definition FROM raw.views "
        "WHERE table_name = 'ORDER_SUMMARY' AND view_definition IS NOT NULL"
    ).fetchall()
    assert vd, "expected a non-null view definition for owned view"
    con.close()


def test_standard_profile(tmp_path, source):
    from md_migration_assessment.report import build_report
    from md_migration_assessment.report.signals import SIGNALS

    con = open_output(str(tmp_path / "standard.duckdb"))
    coll = run_collection(con, source, profile=Profile.STANDARD)
    runs = _runs(con, coll)
    _print_report(runs)

    # 'failed' = real defect (column drift, SQL error). Latency shows up as
    # complete-with-zero-rows, which is acceptable here.
    failed = {n: r for n, r in runs.items() if r["status"] == "failed"}
    assert not failed, f"failed extractors: {failed}"

    for ex in EXTRACTORS:
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
    assert len(feats) == len(SIGNALS)
    broken = [f for f in feats if f[3] and f[3].startswith("probe failed")]
    assert not broken, f"probes broken on real data: {broken}"
    observed = [f"  {f[0]}={f[2]}" for f in feats if f[1] == "observed"]
    print("features observed live:")
    print("\n".join(observed) or "  (none yet — ACCOUNT_USAGE lag)")
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
