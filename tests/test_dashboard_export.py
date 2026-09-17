"""The dashboard export reads report.* and meta.* only, and never leaks raw.*.

Builds a synthetic collection with every column the export selects, plus a
raw.procedures table carrying a sentinel body, runs dashboard/scripts/export.py
against it, and checks the parquet outputs.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import duckdb
import pytest

EXPORT_PY = Path(__file__).resolve().parents[1] / "dashboard" / "scripts" / "export.py"
SENTINEL = "SENTINEL_PROCEDURE_BODY_MUST_NOT_SHIP"


def _load_export_module():
    spec = importlib.util.spec_from_file_location("dashboard_export", EXPORT_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def synthetic_db(tmp_path) -> Path:
    path = tmp_path / "assessment.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA report; CREATE SCHEMA meta; CREATE SCHEMA raw;")
    con.execute("""
        CREATE TABLE report.sizing AS SELECT * FROM (VALUES
          ('DB_A', 'S1', 'T1', 'BASE TABLE', 10::BIGINT, 1000::BIGINT, 100::BIGINT, 500::BIGINT, 0::BIGINT, 1, false),
          (NULL,   NULL, 'T2', 'BASE TABLE', 0::BIGINT,  0::BIGINT,    0::BIGINT,   0::BIGINT,   0::BIGINT, 1, NULL),
          ('SNOWFLAKE', 'ACCOUNT_USAGE', 'T3', 'VIEW', NULL, NULL, NULL, NULL, NULL, NULL, true)
        ) t(table_catalog, table_schema, table_name, table_type, row_count, active_bytes,
            time_travel_bytes, failsafe_bytes, retained_for_clone_bytes, retention_time, is_system)""")
    con.execute("""
        CREATE TABLE report.spend_profile AS SELECT * FROM (VALUES
          ('WH_1', DATE '2030-01-01', 1.5, 1.4, 0.1)
        ) t(warehouse_name, usage_date, credits_used, credits_used_compute, credits_used_cloud_services)""")
    con.execute("""
        CREATE TABLE report.workload_rollup AS SELECT * FROM (VALUES
          ('WH_1', 'PUT_FILES', DATE '2030-01-01', 5::BIGINT, 5::BIGINT, 100::BIGINT, 40.0, 0::BIGINT, 0.0, 0.0, 0::BIGINT, 0::BIGINT, 0::BIGINT),
          ('WH_1', 'MERGE',     DATE '2030-01-01', 2::BIGINT, 2::BIGINT, 900::BIGINT, 800.0, 4096::BIGINT, 2048.0, 4000.0, 1::BIGINT, 0::BIGINT, 30::BIGINT)
        ) t(warehouse_name, query_type, usage_date, n_queries, n_succeeded, sum_elapsed_ms, p95_elapsed_ms,
            sum_bytes_scanned, p50_bytes_scanned, p95_bytes_scanned, n_spilled_local, n_spilled_remote,
            sum_queued_overload_ms)""")
    con.execute("""
        CREATE TABLE report.concurrency_profile AS SELECT * FROM (VALUES
          ('WH_1', TIMESTAMPTZ '2030-01-01 10:00:00+00', 3::BIGINT, 1.2, 600.0)
        ) t(warehouse_name, hour_start, peak_concurrent_queries, avg_concurrent_queries, busy_seconds)""")
    con.execute("""
        CREATE TABLE report.dialect_constructs AS SELECT * FROM (VALUES
          ('flatten', 3::BIGINT, 100::BIGINT, 'query_history_server_side', 'heuristic')
        ) t(construct, n_queries_matched, n_queries_scanned, source, note)""")
    con.execute("""
        CREATE TABLE report.feature_inventory AS SELECT * FROM (VALUES
          ('code', 'stored_procedures', 'observed', 7::BIGINT, false, NULL, ['DB_A.S1.P1'], 'procedures', NULL),
          ('security', 'masking_policies', 'observed_zero', 0::BIGINT, false, NULL, NULL, 'masking_policies', NULL),
          ('table_layout', 'search_optimization', 'unknown', NULL, false, 'extract_failed', NULL, 'search_optimization_history', NULL)
        ) t(category, feature, observation_status, count, lower_bound, unknown_reason, sample_objects,
            source_extractor, note)""")
    con.execute("""
        CREATE TABLE report.ingestion_inventory AS SELECT * FROM (VALUES
          ('DB_A', 'S1', 'T1', 'copy_into', 12::BIGINT, 1000::BIGINT, 65536::BIGINT, 3::BIGINT, 'high')
        ) t(table_catalog, table_schema, table_name, load_method, total_files, total_rows_loaded,
            total_bytes_loaded, days_with_writes, confidence)""")
    con.execute("""
        CREATE TABLE report.tool_fingerprints AS SELECT * FROM (VALUES
          ('JDBC 3.0', 'client_app', 'high', 42::BIGINT, 2::BIGINT, 1000::BIGINT)
        ) t(tool, detection_method, confidence, n_events, n_distinct_users, sum_elapsed_ms)""")
    con.execute("""
        CREATE TABLE meta.extract_runs AS SELECT * FROM (VALUES
          ('tables', 'tables', 'complete', 'account_usage', 3::BIGINT, 'SNOWFLAKE.OBJECT_VIEWER', 'STANDARD', NULL, NULL,
           TIMESTAMPTZ '2030-01-01 00:00:00+00', TIMESTAMPTZ '2030-01-31 00:00:00+00'),
          ('search_optimization_history', 'search_optimization_history', 'failed', 'account_usage', NULL, NULL,
           'ENTERPRISE', 'error', 'SQL compilation error', NULL, NULL)
        ) t(extractor, target_table, status, source_used, rows_written, required_privilege, min_edition,
            error_category, error_detail, actual_window_start, actual_window_end)""")
    con.execute("""
        CREATE VIEW meta.gaps AS
        SELECT extractor, status, source_used, error_category, error_detail
        FROM meta.extract_runs WHERE status <> 'complete'""")
    con.execute("""
        CREATE TABLE meta.collections AS SELECT * FROM (VALUES
          ('0.1.3', 3, 'standard', 30, 'snowflake', 'LOCATOR1', 'AWS_US_EAST_1', NULL,
           TIMESTAMPTZ '2030-02-01 00:00:00+00', TIMESTAMPTZ '2030-02-01 00:10:00+00')
        ) t(tool_version, meta_schema_version, profile, history_days, source_kind, source_deployment,
            source_region, source_edition, started_at, finished_at)""")
    con.execute("""
        CREATE TABLE report.schema_version AS
        SELECT 2 AS report_schema_version, '0.1.3' AS tool_version,
               TIMESTAMPTZ '2030-02-01 00:10:00+00' AS built_at""")
    con.execute(f"""
        CREATE TABLE raw.procedures AS SELECT 'DB_A' AS PROCEDURE_CATALOG, 'P1' AS PROCEDURE_NAME,
               '{SENTINEL}' AS PROCEDURE_DEFINITION""")
    con.close()
    return path


def test_export_writes_every_parquet_with_expected_columns(synthetic_db, tmp_path):
    mod = _load_export_module()
    out = tmp_path / "data"
    written = dict(mod.export(synthetic_db, out))

    expected = {name for name, _ in mod.EXPORTS}
    assert set(written) == expected
    assert {p.stem for p in out.glob("*.parquet")} == expected

    con = duckdb.connect()
    cols = lambda name: [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{out / name}.parquet'").fetchall()]

    # the app filters on these; they must exist and system/null handling must be explicit
    assert "is_system" in cols("sizing") and "is_system" in cols("ingestion_inventory")
    sizing = con.execute(f"SELECT table_catalog, is_system FROM '{out}/sizing.parquet' ORDER BY table_name").fetchall()
    assert sizing == [("DB_A", False), ("(unattributed)", False), ("SNOWFLAKE", True)]

    assert "query_class" in cols("workload_rollup")
    classes = dict(con.execute(f"SELECT query_type, query_class FROM '{out}/workload_rollup.parquet'").fetchall())
    assert classes == {"PUT_FILES": "file operation", "MERGE": "transformation"}

    assert "usage_date" in cols("concurrency_profile")  # shared date brush needs it
    assert {"observation_status", "lower_bound", "unknown_reason"} <= set(cols("feature_inventory"))
    assert {"source_deployment", "source_region"} <= set(cols("collection"))
    assert con.execute(f"SELECT count(*) FROM '{out}/gaps.parquet'").fetchone()[0] == 1


def test_export_never_ships_raw_content(synthetic_db, tmp_path):
    mod = _load_export_module()
    out = tmp_path / "data"
    mod.export(synthetic_db, out)
    for p in out.glob("*.parquet"):
        assert SENTINEL.encode() not in p.read_bytes(), p.name
    # and the SQL itself never touches raw.*
    assert "raw." not in " ".join(sql for _, sql in mod.EXPORTS).lower()


def test_export_opens_source_read_only(synthetic_db, tmp_path):
    mod = _load_export_module()
    before = synthetic_db.stat().st_mtime_ns
    mod.export(synthetic_db, tmp_path / "data")
    assert synthetic_db.stat().st_mtime_ns == before


def test_export_refuses_stale_meta_schema(synthetic_db, tmp_path):
    mod = _load_export_module()
    con = duckdb.connect(str(synthetic_db))
    con.execute("UPDATE meta.collections SET meta_schema_version = 2")
    con.close()
    with pytest.raises(ValueError, match="meta schema v2.*Re-collect"):
        mod.export(synthetic_db, tmp_path / "data")
    assert not (tmp_path / "data").exists()  # refused before writing anything


def test_export_refuses_stale_report_schema(synthetic_db, tmp_path):
    mod = _load_export_module()
    con = duckdb.connect(str(synthetic_db))
    con.execute("DROP TABLE report.schema_version")  # a v1 (pre-versioning) report
    con.close()
    with pytest.raises(ValueError, match="report schema v1.*md-assess assess"):
        mod.export(synthetic_db, tmp_path / "data")


def test_export_versions_track_the_package():
    """The dashboard pins the shapes it was written against; fail loudly here when
    the collector moves on so the SELECTs get reviewed together with the bump."""
    from md_migration_assessment import META_SCHEMA_VERSION
    from md_migration_assessment.report import REPORT_SCHEMA_VERSION

    mod = _load_export_module()
    assert mod.EXPECTED_META_SCHEMA_VERSION == META_SCHEMA_VERSION
    assert mod.EXPECTED_REPORT_SCHEMA_VERSION == REPORT_SCHEMA_VERSION
