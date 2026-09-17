#!/usr/bin/env python3
"""Export view-backing parquet files from an md-assess collection into public/data/.

- Opens the source database READ-ONLY; never mutates it.
- Exports from report.* and meta.* only. Nothing from raw.* is read or shipped
  (raw.procedures holds full stored-procedure bodies; raw.* is unsummarised evidence).
- Re-runnable: overwrites public/data/*.parquet each time.

Usage:  python scripts/export.py [--db path/to/assessment.duckdb] [--out public/data]
Needs only the `duckdb` Python package (already a dependency of md-assess).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# The schema shapes the SELECTs below (and src/) were written against. A file
# written under another version fails here with a re-collect / rebuild message
# instead of a binder error on a column the old shape never had. Bump these
# together with the SELECTs when the collector's meta.* or report.* changes.
EXPECTED_META_SCHEMA_VERSION = 3
EXPECTED_REPORT_SCHEMA_VERSION = 2

# (parquet name, SELECT). Order and column names are what src/ expects.
EXPORTS: list[tuple[str, str]] = [
    # 1. Storage sizing at table grain. is_system is what the app filters on.
    ("sizing", """
        SELECT
          coalesce(table_catalog, '(unattributed)') AS table_catalog,
          coalesce(table_schema,  '(unattributed)') AS table_schema,
          table_name, table_type, row_count,
          active_bytes, time_travel_bytes, failsafe_bytes, retained_for_clone_bytes,
          retention_time,
          coalesce(is_system, false) AS is_system
        FROM report.sizing"""),
    # 2. Spend per (warehouse, day).
    ("spend_profile", """
        SELECT warehouse_name, usage_date,
               credits_used, credits_used_compute, credits_used_cloud_services
        FROM report.spend_profile"""),
    # 3. Workload rollup per (warehouse, query_type, day) plus a derived query_class so
    #    transformation / file-operation / read / metadata work is separable at a glance.
    ("workload_rollup", """
        SELECT
          warehouse_name, query_type, usage_date,
          CASE
            WHEN query_type IN ('PUT_FILES','LIST_FILES','REMOVE_FILES','GET_FILES') THEN 'file operation'
            WHEN query_type IN ('CREATE_TABLE_AS_SELECT','MERGE','INSERT','UPDATE','DELETE',
                                'COPY','UNLOAD','TRUNCATE_TABLE','MULTI_TABLE_INSERT') THEN 'transformation'
            WHEN query_type IN ('SELECT','EXPLAIN','WITH') THEN 'read'
            WHEN query_type IN ('SHOW','DESCRIBE','USE','ALTER_SESSION','COMMIT','BEGIN_TRANSACTION',
                                'ROLLBACK','GET_RESULT','SET','UNSET') THEN 'metadata / session'
            ELSE 'ddl / admin'
          END AS query_class,
          n_queries, n_succeeded, sum_elapsed_ms, p95_elapsed_ms,
          sum_bytes_scanned, p50_bytes_scanned, p95_bytes_scanned,
          n_spilled_local, n_spilled_remote, sum_queued_overload_ms
        FROM report.workload_rollup"""),
    # 4. Concurrency per (warehouse, hour). usage_date added so the shared date brush applies.
    ("concurrency_profile", """
        SELECT warehouse_name, hour_start, CAST(hour_start AS DATE) AS usage_date,
               peak_concurrent_queries, avg_concurrent_queries, busy_seconds
        FROM report.concurrency_profile"""),
    # 5. Dialect constructs (account level).
    ("dialect_constructs", """
        SELECT construct, n_queries_matched, n_queries_scanned, source, note
        FROM report.dialect_constructs"""),
    # 6. Feature inventory with the observed / observed_zero / unknown contract intact.
    ("feature_inventory", """
        SELECT category, feature, observation_status, count, lower_bound, unknown_reason,
               array_to_string(sample_objects, ', ') AS sample_objects,
               source_extractor, note
        FROM report.feature_inventory"""),
    # 7. Ingestion inventory per table. is_system kept (false) so shared filters bind.
    ("ingestion_inventory", """
        SELECT table_catalog, table_schema, table_name, load_method,
               total_files, total_rows_loaded, total_bytes_loaded, days_with_writes, confidence,
               false AS is_system
        FROM report.ingestion_inventory"""),
    # 8. Tool fingerprints.
    ("tool_fingerprints", """
        SELECT tool, detection_method, confidence, n_events, n_distinct_users, sum_elapsed_ms
        FROM report.tool_fingerprints"""),
    # 9. Coverage: extractor runs and gaps. error_detail is a Snowflake error string, not data.
    ("extract_runs", """
        SELECT extractor, target_table, status, source_used, rows_written,
               required_privilege, min_edition, error_category, left(error_detail, 300) AS error_detail,
               actual_window_start, actual_window_end
        FROM meta.extract_runs"""),
    ("gaps", """
        SELECT extractor, status, source_used, error_category, left(error_detail, 300) AS error_detail
        FROM meta.gaps"""),
    # 10. Collection metadata for the header (account locator + region, tool version, window).
    ("collection", """
        SELECT tool_version, profile, history_days, source_kind, source_deployment, source_region,
               source_edition, started_at, finished_at
        FROM meta.collections"""),
]


def _table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
    return bool(con.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchone())


def check_versions(con: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    """Refuse a collection whose meta.* or report.* shapes differ from what the
    SELECTs below expect. Mirrors the CLI's own checks (db.check_meta_version,
    report.check_report_version) so the guidance is the same."""
    if not _table_exists(con, "meta", "collections"):
        raise ValueError(f"{db_path} is not an md-assess collection (no meta.collections)")
    versions = {v for (v,) in con.execute(
        "SELECT DISTINCT meta_schema_version FROM meta.collections").fetchall()}
    stale = sorted(v for v in versions if v != EXPECTED_META_SCHEMA_VERSION)
    if stale:
        raise ValueError(
            f"{db_path} has meta schema v{stale[0]}; this dashboard reads "
            f"v{EXPECTED_META_SCHEMA_VERSION}. Re-collect into a new file with a "
            "current md-assess (explicit migrations are not provided pre-1.0).")
    if not _table_exists(con, "report", "feature_inventory"):
        raise ValueError(
            f"{db_path} has no report.* layer yet; build it with: md-assess assess --db {db_path}")
    stored = 1  # a report without report.schema_version predates versioning (v0.1.2 and earlier)
    if _table_exists(con, "report", "schema_version"):
        row = con.execute("SELECT report_schema_version FROM report.schema_version").fetchone()
        stored = row[0] if row else 1
    if stored != EXPECTED_REPORT_SCHEMA_VERSION:
        raise ValueError(
            f"report.* in {db_path} was built with report schema v{stored}; this dashboard "
            f"reads v{EXPECTED_REPORT_SCHEMA_VERSION}. The raw evidence is unaffected; rebuild "
            f"the report with: md-assess assess --db {db_path}")


def export(db_path: Path, out_dir: Path) -> list[tuple[str, int]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"source database not found: {db_path}")
    con = duckdb.connect(str(db_path), read_only=True)
    written: list[tuple[str, int]] = []
    try:
        check_versions(con, db_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        for old in out_dir.glob("*.parquet"):
            old.unlink()
        for name, select in EXPORTS:
            target = out_dir / f"{name}.parquet"
            path_literal = str(target).replace("'", "''")
            con.execute(f"COPY ({select}) TO '{path_literal}' (FORMAT PARQUET, COMPRESSION ZSTD)")
            written.append((name, target.stat().st_size))
    finally:
        con.close()
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=ROOT / "assessment.duckdb", help="md-assess output database (read-only)")
    ap.add_argument("--out", type=Path, default=ROOT / "public" / "data", help="destination folder for parquet files")
    args = ap.parse_args()
    try:
        written = export(args.db, args.out)
    except (FileNotFoundError, ValueError) as exc:
        sys.exit(str(exc))
    total = 0
    print(f"exported to {args.out}:")
    for name, size in written:
        total += size
        print(f"  {size / 1024:8.1f} KB  {name}.parquet")
    print(f"total shipped parquet: {total / 1024 / 1024:.2f} MB")


if __name__ == "__main__":
    main()
