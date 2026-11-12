"""Databricks report fact builders (adapter-owned ``report.*`` relations).

- ``report.sizing``: per table, type, format, bytes and files.
- ``report.collection_warnings``: per table, governance that changes what a
  reader sees (row filters, column masks). Reading such a table as a
  principal the policy does not exempt returns filtered rows or masked
  values, so a migration copy must run as an exempt principal.
- ``report.readable_in_place``: per table, how MotherDuck could read it
  without copying (Unity Catalog Iceberg REST for Iceberg and UniForm
  tables, a direct Delta scan for external Delta), or what has to happen
  first. Facts and a read path only, no effort scores.

Like every fact builder: the relation always exists with a stable shape;
when the source extracts are missing it is empty and meta.extract_runs
says why.
"""

from __future__ import annotations

import duckdb

from ...report.facts import table_exists

_SYSTEM = (
    "(t.table_catalog IN ('system', 'samples', 'hive_metastore', '__databricks_internal') "
    "OR t.table_name LIKE '\\_\\_materialization\\_%' ESCAPE '\\' "
    "OR t.table_name LIKE 'event\\_log\\_%' ESCAPE '\\')"
)

_SIZING_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.sizing;
CREATE TABLE report.sizing (
    collection_id UUID, table_catalog VARCHAR, table_schema VARCHAR,
    table_name VARCHAR, table_type VARCHAR, data_source_format VARCHAR,
    bytes BIGINT, num_files BIGINT, is_system BOOLEAN,
    tables_extract_status VARCHAR, details_extract_status VARCHAR,
    -- Snowflake-shaped storage split the Dive reads: Delta reports one
    -- current-version size, so time-travel/fail-safe/clone bytes are unknown.
    active_bytes BIGINT, time_travel_bytes BIGINT, failsafe_bytes BIGINT,
    retained_for_clone_bytes BIGINT
);
"""

_READ_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.readable_in_place;
CREATE TABLE report.readable_in_place (
    collection_id UUID, table_catalog VARCHAR, table_schema VARCHAR,
    table_name VARCHAR, table_type VARCHAR, data_source_format VARCHAR,
    uniform_formats VARCHAR, storage_scheme VARCHAR, read_path VARCHAR,
    note VARCHAR, tables_extract_status VARCHAR, details_extract_status VARCHAR
);
"""


def _details_cols(con: duckdb.DuckDBPyConnection) -> tuple[str, str]:
    """(select list, join) for raw.table_details, or NULL stand-ins."""
    if not table_exists(con, "raw", "table_details"):
        return (
            "CAST(NULL AS BIGINT) AS bytes, CAST(NULL AS BIGINT) AS num_files, "
            "CAST(NULL AS VARCHAR) AS uniform_formats, CAST(NULL AS VARCHAR) AS location, "
            "CAST(NULL AS VARCHAR) AS detail_format",
            "",
        )
    return (
        "d.size_in_bytes::BIGINT AS bytes, d.num_files::BIGINT AS num_files, "
        "d.uniform_formats AS uniform_formats, d.location AS location, "
        "d.format AS detail_format",
        "LEFT JOIN raw.table_details d ON d.collection_id = t.collection_id "
        "AND d.table_catalog = t.table_catalog AND d.table_schema = t.table_schema "
        "AND d.table_name = t.table_name",
    )


_BASE = """
    FROM raw.tables t
    LEFT JOIN meta.extract_runs tr
        ON tr.collection_id = t.collection_id AND tr.extractor = 'tables'
    LEFT JOIN meta.extract_runs dr
        ON dr.collection_id = t.collection_id AND dr.extractor = 'table_details'
    {join}
"""


def _build_sizing(con: duckdb.DuckDBPyConnection) -> None:
    """Bytes and file count per table, views left out.

    Delta gives one current size, so time-travel, fail-safe and clone bytes stay unknown.
    """
    con.execute(_SIZING_DDL)
    if not table_exists(con, "raw", "tables"):
        return
    cols, join = _details_cols(con)
    con.execute(f"""
        INSERT INTO report.sizing BY NAME
        SELECT * EXCLUDE (uniform_formats, location, detail_format) FROM (
            SELECT t.collection_id AS collection_id,
                   t.table_catalog AS table_catalog, t.table_schema AS table_schema,
                   t.table_name AS table_name, t.table_type AS table_type,
                   t.data_source_format AS data_source_format,
                   {cols},
                   {_SYSTEM} AS is_system,
                   tr.status AS tables_extract_status,
                   coalesce(dr.status, 'unavailable') AS details_extract_status
            {_BASE.format(join=join)}
            WHERE t.table_type NOT IN ('VIEW')
        )
    """)
    con.execute("UPDATE report.sizing SET active_bytes = bytes")


def _build_readable_in_place(con: duckdb.DuckDBPyConnection) -> None:
    """How each table could be read from MotherDuck without a copy, or what has to happen first."""
    con.execute(_READ_DDL)
    if not table_exists(con, "raw", "tables"):
        return
    cols, join = _details_cols(con)
    con.execute(f"""
        INSERT INTO report.readable_in_place BY NAME
        WITH base AS (
            SELECT t.collection_id AS collection_id,
                   t.table_catalog AS table_catalog, t.table_schema AS table_schema,
                   t.table_name AS table_name, t.table_type AS table_type,
                   t.data_source_format AS data_source_format,
                   {cols},
                   coalesce(location, t.storage_path) AS path,
                   tr.status AS tables_extract_status,
                   coalesce(dr.status, 'unavailable') AS details_extract_status
            {_BASE.format(join=join)}
            WHERE NOT {_SYSTEM}
        ), typed AS (
            SELECT *, upper(coalesce(data_source_format, detail_format, '')) AS fmt
            FROM base
        ), classified AS (
            SELECT *,
                lower(regexp_extract(coalesce(path, ''), '^([a-zA-Z0-9]+)://', 1)) AS storage_scheme,
                CASE
                    WHEN table_type IN ('VIEW', 'MATERIALIZED_VIEW', 'STREAMING_TABLE')
                        THEN 'recreate'
                    WHEN table_type = 'FOREIGN' THEN 'read_from_origin'
                    WHEN fmt = 'ICEBERG'
                        OR lower(coalesce(uniform_formats, '')) LIKE '%iceberg%'
                        THEN 'uc_iceberg_rest'
                    WHEN fmt = 'DELTA' AND table_type LIKE 'EXTERNAL%' THEN 'delta_scan'
                    -- Without complete table details, a missing UniForm list means "not looked at", not "none".
                    WHEN fmt = 'DELTA' AND uniform_formats IS NULL
                        AND details_extract_status <> 'complete' THEN 'unknown'
                    WHEN fmt = 'DELTA' THEN 'enable_uniform_or_copy'
                    WHEN fmt IN ('PARQUET', 'CSV', 'JSON') THEN 'file_scan'
                    ELSE 'copy'
                END AS read_path
            FROM typed
        )
        SELECT collection_id, table_catalog, table_schema, table_name, table_type,
               data_source_format, uniform_formats,
               nullif(storage_scheme, '') AS storage_scheme, read_path,
               CASE
                   WHEN read_path = 'recreate'
                       THEN 'derived object: rebuild from its definition'
                   WHEN read_path = 'unknown'
                       THEN 'UniForm unknown: table_details not complete'
                   WHEN read_path = 'enable_uniform_or_copy'
                       THEN 'managed Delta without UniForm: enable Iceberg reads or copy'
                   WHEN storage_scheme IN ('abfss', 'gs', 'wasbs')
                       THEN 'storage on ' || storage_scheme || ': cross-cloud read'
               END AS note,
               tables_extract_status, details_extract_status
        FROM classified
    """)


_WARN_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.collection_warnings;
CREATE TABLE report.collection_warnings (
    collection_id UUID, warning VARCHAR, table_catalog VARCHAR,
    table_schema VARCHAR, table_name VARCHAR, policy_function VARCHAR,
    columns VARCHAR, detail VARCHAR
);
"""

_WARNINGS = {
    "row_filters": (
        "row_filter", "filter_name", "target_columns",
        "rows are filtered for principals the policy does not exempt; "
        "copy or validate as an exempt principal or rows will be missing",
    ),
    "column_masks": (
        "column_mask", "mask_name", "column_name",
        "values are masked for principals the policy does not exempt; "
        "copy as an exempt principal or masked values will be migrated",
    ),
}


def _build_collection_warnings(con: duckdb.DuckDBPyConnection) -> None:
    """Flag tables with a row filter or column mask.

    A copy has to run as a principal the policy exempts, or rows go missing.
    """
    con.execute(_WARN_DDL)
    for source, (warning, fn_col, cols_col, detail) in _WARNINGS.items():
        if not table_exists(con, "raw", source):
            continue
        con.execute(f"""
            INSERT INTO report.collection_warnings BY NAME
            SELECT collection_id, '{warning}' AS warning, table_catalog,
                   table_schema, table_name, {fn_col} AS policy_function,
                   {cols_col}::VARCHAR AS columns, '{detail}' AS detail
            FROM raw.{source}
        """)
    # One line saying which workspaces spend, queries and jobs cover.
    if table_exists(con, "raw", "workspaces"):
        con.execute("""
            INSERT INTO report.collection_warnings BY NAME
            SELECT any_value(collection_id) AS collection_id,
                   'workspace_scope' AS warning,
                   'spend, queries and jobs cover ' || count(*) || ' workspace(s): '
                   || string_agg(coalesce(workspace_name, '?') || ' (' || workspace_id || ')', ', '
                                 ORDER BY workspace_id)
                   || '. Set DATABRICKS_WORKSPACES to change this.' AS detail
            FROM raw.workspaces
            HAVING count(*) > 0
        """)


# ── workload facts in the neutral report shapes the Dive reads ────────────
# Same relation names and columns as the Snowflake adapter so the existing
# dashboard renders a Databricks collection unchanged. "warehouse_name" is
# the SQL warehouse name when the work ran on one, "serverless compute" for
# serverless jobs and notebooks, else the billing product or compute type. Databricks "credits" are DBUs.


# Serverless jobs and notebooks show up in billing by product and in query
# history as SERVERLESS_COMPUTE. Both get this one name so the Dive's
# warehouse filter matches across the spend and workload panels.
_SERVERLESS = "serverless compute"


def _status(con: duckdb.DuckDBPyConnection, extractor: str) -> str:
    """SQL for the run status of one extract in the same collection."""
    return (
        f"(SELECT any_value(status) FROM meta.extract_runs r "
        f"WHERE r.collection_id = x.collection_id AND r.extractor = '{extractor}')"
    )


def _wh_join(con: duckdb.DuckDBPyConnection, alias: str = "x") -> tuple[str, str]:
    """Name column and join that turn warehouse ids into names. The name is NULL without a warehouses extract."""
    if not table_exists(con, "raw", "warehouses"):
        return "CAST(NULL AS VARCHAR)", ""
    return (
        "w.warehouse_name",
        f"LEFT JOIN raw.warehouses w ON w.collection_id = {alias}.collection_id "
        f"AND w.warehouse_id = {alias}.warehouse_id",
    )


_SPEND_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.spend_profile;
CREATE TABLE report.spend_profile (
    collection_id UUID, warehouse_name VARCHAR, usage_date DATE,
    credits_used DOUBLE, credits_used_compute DOUBLE,
    credits_used_cloud_services DOUBLE, list_cost DOUBLE,
    billing_origin_product VARCHAR, metering_extract_status VARCHAR
);
"""


def _build_spend_profile(con: duckdb.DuckDBPyConnection) -> None:
    """Daily DBUs and list cost per warehouse or product. Rows in other units are left out."""
    con.execute(_SPEND_DDL)
    if not table_exists(con, "raw", "billing_usage_daily"):
        return
    name, join = _wh_join(con)
    con.execute(f"""
        INSERT INTO report.spend_profile BY NAME
        SELECT x.collection_id AS collection_id,
               coalesce({name}, x.warehouse_id,
                        CASE WHEN x.is_serverless AND x.billing_origin_product IN ('JOBS', 'INTERACTIVE', 'ALL_PURPOSE')
                             THEN '{_SERVERLESS}' END,
                        lower(x.billing_origin_product)) AS warehouse_name,
               x.usage_date AS usage_date,
               sum(x.usage_quantity)::DOUBLE AS credits_used,
               sum(x.usage_quantity)::DOUBLE AS credits_used_compute,
               0::DOUBLE AS credits_used_cloud_services,
               sum(x.list_cost)::DOUBLE AS list_cost,
               any_value(x.billing_origin_product) AS billing_origin_product,
               {_status(con, 'billing_usage_daily')} AS metering_extract_status
        FROM raw.billing_usage_daily x {join}
        WHERE upper(coalesce(x.usage_unit, 'DBU')) = 'DBU'
        GROUP BY x.collection_id, 2, x.usage_date
    """)


_ROLLUP_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.workload_rollup;
CREATE TABLE report.workload_rollup (
    collection_id UUID, warehouse_name VARCHAR, query_type VARCHAR,
    usage_date DATE, n_queries BIGINT, n_succeeded BIGINT,
    sum_elapsed_ms BIGINT, p95_elapsed_ms DOUBLE,
    sum_bytes_scanned BIGINT, p50_bytes_scanned DOUBLE,
    p95_bytes_scanned DOUBLE, n_spilled_local BIGINT,
    n_spilled_remote BIGINT, sum_bytes_spilled_local BIGINT,
    sum_bytes_spilled_remote BIGINT, sum_queued_overload_ms BIGINT,
    n_from_result_cache BIGINT, rollup_extract_status VARCHAR
);
"""


def _build_workload_rollup(con: duckdb.DuckDBPyConnection) -> None:
    """Daily query counts, time, bytes and spill per warehouse and statement type.

    Remote spill is not collected, so those columns are NULL.
    """
    con.execute(_ROLLUP_DDL)
    if not table_exists(con, "raw", "query_workload_rollup"):
        return
    name, join = _wh_join(con)
    con.execute(f"""
        INSERT INTO report.workload_rollup BY NAME
        SELECT x.collection_id AS collection_id,
               coalesce({name}, x.warehouse_id,
                        CASE WHEN x.compute_type = 'SERVERLESS_COMPUTE' THEN '{_SERVERLESS}' END,
                        x.compute_type, 'unknown') AS warehouse_name,
               coalesce(x.statement_type, 'OTHER') AS query_type,
               x.usage_date AS usage_date,
               x.n_queries::BIGINT AS n_queries, x.n_succeeded::BIGINT AS n_succeeded,
               x.sum_duration_ms::BIGINT AS sum_elapsed_ms,
               x.p95_duration_ms::DOUBLE AS p95_elapsed_ms,
               x.sum_read_bytes::BIGINT AS sum_bytes_scanned,
               x.p50_read_bytes::DOUBLE AS p50_bytes_scanned,
               x.p95_read_bytes::DOUBLE AS p95_bytes_scanned,
               x.n_spilled_local::BIGINT AS n_spilled_local,
               CAST(NULL AS BIGINT) AS n_spilled_remote,
               x.sum_spilled_local_bytes::BIGINT AS sum_bytes_spilled_local,
               CAST(NULL AS BIGINT) AS sum_bytes_spilled_remote,
               x.sum_waiting_at_capacity_ms::BIGINT AS sum_queued_overload_ms,
               x.n_from_result_cache::BIGINT AS n_from_result_cache,
               {_status(con, 'query_workload_rollup')} AS rollup_extract_status
        FROM raw.query_workload_rollup x {join}
    """)


_CONC_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.concurrency_profile;
CREATE TABLE report.concurrency_profile (
    collection_id UUID, warehouse_name VARCHAR, hour_start TIMESTAMPTZ,
    peak_concurrent_queries BIGINT, avg_concurrent_queries DOUBLE,
    busy_seconds DOUBLE, concurrency_extract_status VARCHAR
);
"""


def _build_concurrency_profile(con: duckdb.DuckDBPyConnection) -> None:
    """Peak concurrent queries per warehouse per hour. Average and busy time are NULL."""
    con.execute(_CONC_DDL)
    if not table_exists(con, "raw", "query_concurrency"):
        return
    name, join = _wh_join(con)
    con.execute(f"""
        INSERT INTO report.concurrency_profile BY NAME
        SELECT x.collection_id AS collection_id,
               coalesce({name}, x.warehouse_id) AS warehouse_name,
               x.hour_start AS hour_start,
               x.peak_concurrent_queries::BIGINT AS peak_concurrent_queries,
               CAST(NULL AS DOUBLE) AS avg_concurrent_queries,
               CAST(NULL AS DOUBLE) AS busy_seconds,
               {_status(con, 'query_concurrency')} AS concurrency_extract_status
        FROM raw.query_concurrency x {join}
    """)


_TOOLS_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.tool_fingerprints;
CREATE TABLE report.tool_fingerprints (
    collection_id UUID, tool VARCHAR, detection_method VARCHAR,
    confidence VARCHAR, n_events BIGINT, n_distinct_users BIGINT,
    sum_elapsed_ms BIGINT,
    first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ,
    window_start TIMESTAMPTZ, window_end TIMESTAMPTZ,
    extract_status VARCHAR, note VARCHAR
);
"""


def _build_tool_fingerprints(con: duckdb.DuckDBPyConnection) -> None:
    """Which client tools run queries, with how each was detected and how sure we are."""
    con.execute(_TOOLS_DDL)
    if not table_exists(con, "raw", "client_app_fingerprints"):
        return
    # n_distinct_users is a max over days: a floor on distinct users in the
    # window (the raw extract counts distinct users per day).
    con.execute(f"""
        INSERT INTO report.tool_fingerprints BY NAME
        SELECT x.collection_id AS collection_id,
               coalesce(x.client_application, split_part(x.client_driver, ',', 1),
                        lower(x.compute_type) || ' (no client tag)') AS tool,
               CASE WHEN x.client_application IS NOT NULL THEN 'query_history.client_application'
                    WHEN x.client_driver IS NOT NULL THEN 'query_history.client_driver'
                    ELSE 'query_history.compute_type' END AS detection_method,
               CASE WHEN x.client_application IS NOT NULL THEN 'high' ELSE 'medium' END AS confidence,
               sum(x.n_queries)::BIGINT AS n_events,
               max(x.n_distinct_users)::BIGINT AS n_distinct_users,
               sum(x.sum_duration_ms)::BIGINT AS sum_elapsed_ms,
               min(x.first_seen) AS first_seen, max(x.last_seen) AS last_seen,
               any_value(r.actual_window_start) AS window_start,
               any_value(r.actual_window_end) AS window_end,
               any_value(r.status) AS extract_status,
               'n_distinct_users is the busiest day, a floor for the window' AS note
        FROM raw.client_app_fingerprints x
        LEFT JOIN meta.extract_runs r
            ON r.collection_id = x.collection_id AND r.extractor = 'client_app_fingerprints'
        GROUP BY x.collection_id, 2, 3, 4
    """)


_INGEST_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.ingestion_inventory;
CREATE TABLE report.ingestion_inventory (
    collection_id UUID, table_catalog VARCHAR, table_schema VARCHAR,
    table_name VARCHAR, load_method VARCHAR,
    days_with_writes BIGINT, total_files BIGINT, total_rows_loaded BIGINT,
    total_bytes_loaded BIGINT, files_per_observed_day DOUBLE,
    first_load_time TIMESTAMPTZ, last_load_time TIMESTAMPTZ,
    detection_method VARCHAR, confidence VARCHAR,
    supporting_event_count BIGINT,
    window_start TIMESTAMPTZ, window_end TIMESTAMPTZ,
    last_observed_at TIMESTAMPTZ,
    copy_history_extract_status VARCHAR, note VARCHAR
);
"""


def _build_ingestion_inventory(con: duckdb.DuckDBPyConnection) -> None:
    """Tables written by jobs, pipelines, notebooks or SQL, from lineage.

    Lineage says who writes a table, not how much: file/row/byte counts stay
    NULL (unknown), never zero.
    """
    con.execute(_INGEST_DDL)
    if not table_exists(con, "raw", "table_lineage_rollup"):
        return
    con.execute(f"""
        INSERT INTO report.ingestion_inventory BY NAME
        SELECT x.collection_id AS collection_id,
               split_part(x.target_table_full_name, '.', 1) AS table_catalog,
               split_part(x.target_table_full_name, '.', 2) AS table_schema,
               split_part(x.target_table_full_name, '.', 3) AS table_name,
               lower(coalesce(x.entity_type, 'unattributed')) AS load_method,
               max(x.n_active_days)::BIGINT AS days_with_writes,
               CAST(NULL AS BIGINT) AS total_files,
               CAST(NULL AS BIGINT) AS total_rows_loaded,
               CAST(NULL AS BIGINT) AS total_bytes_loaded,
               CAST(NULL AS DOUBLE) AS files_per_observed_day,
               min(x.first_seen)::TIMESTAMPTZ AS first_load_time,
               max(x.last_seen)::TIMESTAMPTZ AS last_load_time,
               'table_lineage' AS detection_method,
               'medium' AS confidence,
               sum(x.n_events)::BIGINT AS supporting_event_count,
               any_value(r.actual_window_start) AS window_start,
               any_value(r.actual_window_end) AS window_end,
               any_value(r.finished_at) AS last_observed_at,
               any_value(r.status) AS copy_history_extract_status,
               'writer from system.access.table_lineage; volumes not in lineage' AS note
        FROM raw.table_lineage_rollup x
        LEFT JOIN meta.extract_runs r
            ON r.collection_id = x.collection_id AND r.extractor = 'table_lineage_rollup'
        WHERE x.target_table_full_name IS NOT NULL
          AND split_part(x.target_table_full_name, '.', 1) NOT IN {_SYSTEM_CATALOGS}
        GROUP BY x.collection_id, 2, 3, 4, 5
    """)


_DIALECT_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.dialect_constructs;
CREATE TABLE report.dialect_constructs (
    collection_id UUID, construct VARCHAR, n_queries_matched BIGINT,
    n_queries_scanned BIGINT, source VARCHAR,
    dialect_extract_status VARCHAR, note VARCHAR
);
"""

_DIALECT_COLUMNS = [
    ("lateral_view_explode", "n_lateral_view"),
    ("higher_order_lambda", "n_lambda"),
    ("colon_path_variant_access", "n_variant_path"),
    ("time_travel_version_timestamp_as_of", "n_time_travel"),
    ("merge_into", "n_merge"),
    ("pivot_unpivot", "n_pivot_unpivot"),
    ("read_files_or_stream_sources", "n_file_stream_sources"),
    ("apply_changes_auto_cdc", "n_apply_changes"),
    ("table_changes_cdf_reads", "n_change_feed_reads"),
    ("ai_functions", "n_ai_functions"),
    ("json_functions", "n_json_functions"),
    ("identifier_fn", "n_identifier_fn"),
    ("optimize_vacuum", "n_delta_maintenance"),
]

_DIALECT_NOTE = (
    "server-side pattern heuristic over workload SQL (query text never "
    "collected); counts are frequency signals, not parse results"
)


def _build_dialect_constructs(con: duckdb.DuckDBPyConnection) -> None:
    """Count of queries using each Databricks-specific SQL construct. Counts only, never query text."""
    con.execute(_DIALECT_DDL)
    if not table_exists(con, "raw", "query_dialect_constructs"):
        return
    for construct, column in _DIALECT_COLUMNS:
        con.execute(f"""
            INSERT INTO report.dialect_constructs BY NAME
            SELECT d.collection_id AS collection_id, '{construct}' AS construct,
                   sum(d."{column}")::BIGINT AS n_queries_matched,
                   sum(d.n_queries_scanned)::BIGINT AS n_queries_scanned,
                   'query_history_server_side' AS source,
                   any_value(r.status) AS dialect_extract_status,
                   '{_DIALECT_NOTE}' AS note
            FROM raw.query_dialect_constructs d
            LEFT JOIN meta.extract_runs r
                ON r.collection_id = d.collection_id
                AND r.extractor = 'query_dialect_constructs'
            GROUP BY d.collection_id
        """)


_SYSTEM_CATALOGS = "('system', 'samples', 'hive_metastore', '__databricks_internal')"

FACT_BUILDERS = (
    _build_sizing,
    _build_readable_in_place,
    _build_collection_warnings,
    _build_spend_profile,
    _build_workload_rollup,
    _build_concurrency_profile,
    _build_tool_fingerprints,
    _build_ingestion_inventory,
    _build_dialect_constructs,
)
