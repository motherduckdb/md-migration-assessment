"""Snowflake report fact builders (adapter-owned ``report.*`` relations).

``report.sizing``, the M3b workload facts (``report.spend_profile``,
``report.workload_profile``, ``report.ingestion_inventory``), and the M3c
aggregate facts (``report.concurrency_profile``, ``report.tool_fingerprints``,
``report.query_shapes``, ``report.workload_rollup``,
``report.dialect_constructs``). Facts only — no compatibility ratings.

These columns speak Snowflake (credits, warehouses, time-travel bytes,
copy history). They stay adapter-owned until a second source shows which
of them are genuinely common; ``report.feature_inventory`` is the
cross-source contract and is built by the neutral report layer.

Like every fact builder: the relation always exists with a stable shape;
when the source extracts are missing it is empty and meta.extract_runs
says why.
"""

from __future__ import annotations

import duckdb

from ...report.facts import table_exists

_SIZING_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.sizing;
CREATE TABLE report.sizing (
    collection_id UUID, table_catalog VARCHAR, table_schema VARCHAR,
    table_name VARCHAR, table_type VARCHAR, row_count BIGINT, bytes BIGINT,
    active_bytes BIGINT, time_travel_bytes BIGINT, failsafe_bytes BIGINT,
    retained_for_clone_bytes BIGINT, retention_time INTEGER, is_system BOOLEAN,
    tables_extract_status VARCHAR, storage_extract_status VARCHAR
);
"""


def _build_sizing(con: duckdb.DuckDBPyConnection) -> None:
    # The relation always exists with a stable shape; when table evidence is
    # entirely missing it is empty and meta.extract_runs explains why —
    # "relation does not exist" is not an acceptable coverage signal.
    con.execute(_SIZING_DDL)
    if not table_exists(con, "raw", "tables"):
        return
    storage_join = ""
    storage_cols = (
        "CAST(NULL AS BIGINT) AS active_bytes, CAST(NULL AS BIGINT) AS time_travel_bytes, "
        "CAST(NULL AS BIGINT) AS failsafe_bytes, CAST(NULL AS BIGINT) AS retained_for_clone_bytes"
    )
    if table_exists(con, "raw", "table_storage_metrics"):
        storage_cols = (
            "s.active_bytes::BIGINT AS active_bytes, "
            "s.time_travel_bytes::BIGINT AS time_travel_bytes, "
            "s.failsafe_bytes::BIGINT AS failsafe_bytes, "
            "s.retained_for_clone_bytes::BIGINT AS retained_for_clone_bytes"
        )
        storage_join = (
            "LEFT JOIN raw.table_storage_metrics s ON s.collection_id = t.collection_id "
            "AND s.table_catalog = t.table_catalog AND s.table_schema = t.table_schema "
            "AND s.table_name = t.table_name"
        )
    con.execute(f"""
        INSERT INTO report.sizing BY NAME
        SELECT
            t.collection_id AS collection_id,
            t.table_catalog AS table_catalog, t.table_schema AS table_schema,
            t.table_name AS table_name, t.table_type AS table_type,
            t.row_count::BIGINT AS row_count,
            t.bytes::BIGINT AS bytes,
            {storage_cols},
            t.retention_time::INTEGER AS retention_time,
            (t.table_catalog = 'SNOWFLAKE' OR t.table_catalog LIKE 'USER$%') AS is_system,
            -- coverage travels with the rows: partial table coverage or an
            -- unavailable storage extract must be visible, not implied
            tr.status AS tables_extract_status,
            coalesce(sr.status, 'unavailable') AS storage_extract_status
        FROM raw.tables t
        LEFT JOIN meta.extract_runs tr
            ON tr.collection_id = t.collection_id AND tr.extractor = 'tables'
        LEFT JOIN meta.extract_runs sr
            ON sr.collection_id = t.collection_id AND sr.extractor = 'table_storage_metrics'
        {storage_join}
        WHERE t.table_type IN ('BASE TABLE', 'MATERIALIZED VIEW')
    """)


# ── M3b: workload facts from aggregate histories (spec §5.3 as amended) ────
# Per-query facts (query-type mix, bytes-scanned percentiles, per-minute
# concurrency, spill rates) are deliberately absent until M3c (decision 15).
# Like sizing, these relations always exist with a stable shape; when the
# source extracts are missing (e.g. a standard-profile collection) they are
# empty and meta.extract_runs says why.

_SPEND_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.spend_profile;
CREATE TABLE report.spend_profile (
    collection_id UUID, warehouse_name VARCHAR, usage_date DATE,
    credits_used DOUBLE, credits_used_compute DOUBLE,
    credits_used_cloud_services DOUBLE,
    metering_extract_status VARCHAR
);
"""


def _build_spend_profile(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SPEND_DDL)
    if not table_exists(con, "raw", "warehouse_metering_history"):
        return
    con.execute("""
        INSERT INTO report.spend_profile BY NAME
        SELECT
            m.collection_id AS collection_id,
            m.warehouse_name AS warehouse_name,
            CAST(m.start_time AS DATE) AS usage_date,
            sum(m.credits_used)::DOUBLE AS credits_used,
            sum(m.credits_used_compute)::DOUBLE AS credits_used_compute,
            sum(m.credits_used_cloud_services)::DOUBLE AS credits_used_cloud_services,
            r.status AS metering_extract_status
        FROM raw.warehouse_metering_history m
        LEFT JOIN meta.extract_runs r
            ON r.collection_id = m.collection_id
            AND r.extractor = 'warehouse_metering_history'
        GROUP BY ALL
    """)


_WORKLOAD_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.workload_profile;
CREATE TABLE report.workload_profile (
    collection_id UUID, warehouse_name VARCHAR, hour_start TIMESTAMPTZ,
    avg_running DOUBLE, avg_queued_load DOUBLE, avg_queued_provisioning DOUBLE,
    avg_blocked DOUBLE, peak_avg_running DOUBLE, credits_used DOUBLE,
    load_extract_status VARCHAR, metering_extract_status VARCHAR
);
"""


def _build_workload_profile(con: duckdb.DuckDBPyConnection) -> None:
    # Warehouse-hour grain: averages of the source view's interval averages.
    # peak_avg_running is an interval-averaged floor for the true peak — real
    # per-minute concurrency peaks need per-query events (M3c).
    con.execute(_WORKLOAD_DDL)
    if not table_exists(con, "raw", "warehouse_load_history"):
        return
    credits_col = "CAST(NULL AS DOUBLE) AS credits_used"
    metering_join = ""
    if table_exists(con, "raw", "warehouse_metering_history"):
        credits_col = "m.credits_used::DOUBLE AS credits_used"
        metering_join = (
            "LEFT JOIN raw.warehouse_metering_history m "
            "ON m.collection_id = l.collection_id "
            "AND m.warehouse_name = l.warehouse_name "
            "AND date_trunc('hour', m.start_time::TIMESTAMPTZ) = l.hour_start::TIMESTAMPTZ"
        )
    con.execute(f"""
        INSERT INTO report.workload_profile BY NAME
        SELECT
            l.collection_id AS collection_id,
            l.warehouse_name AS warehouse_name,
            l.hour_start::TIMESTAMPTZ AS hour_start,
            l.avg_running::DOUBLE AS avg_running,
            l.avg_queued_load::DOUBLE AS avg_queued_load,
            l.avg_queued_provisioning::DOUBLE AS avg_queued_provisioning,
            l.avg_blocked::DOUBLE AS avg_blocked,
            l.peak_avg_running::DOUBLE AS peak_avg_running,
            {credits_col},
            lr.status AS load_extract_status,
            coalesce(mr.status, 'unavailable') AS metering_extract_status
        FROM raw.warehouse_load_history l
        LEFT JOIN meta.extract_runs lr
            ON lr.collection_id = l.collection_id
            AND lr.extractor = 'warehouse_load_history'
        LEFT JOIN meta.extract_runs mr
            ON mr.collection_id = l.collection_id
            AND mr.extractor = 'warehouse_metering_history'
        {metering_join}
    """)


_INGESTION_DDL = """
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

# Every inferred row carries provenance (spec §3): here detection is
# authoritative load metadata, so confidence is 'high'. No stale_candidate
# labels in M3b: absence of COPY/Snowpipe writes does not mean an unwritten
# table (MERGE/INSERT/CTAS cadence needs per-query history — M3c).
_INGESTION_NOTE = (
    "authoritative copy/snowpipe load metadata; MERGE/INSERT/CTAS write "
    "cadence requires per-query history (M3c) and is not_requested in this "
    "collection"
)


def _build_ingestion_inventory(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_INGESTION_DDL)
    if not table_exists(con, "raw", "copy_history"):
        return
    con.execute(f"""
        INSERT INTO report.ingestion_inventory BY NAME
        SELECT
            c.collection_id AS collection_id,
            c.table_catalog AS table_catalog,
            c.table_schema AS table_schema,
            c.table_name AS table_name,
            c.load_method AS load_method,
            count(DISTINCT c.load_date)::BIGINT AS days_with_writes,
            sum(c.n_files)::BIGINT AS total_files,
            sum(c.rows_loaded)::BIGINT AS total_rows_loaded,
            sum(c.bytes_loaded)::BIGINT AS total_bytes_loaded,
            (sum(c.n_files)::DOUBLE / greatest(1, count(DISTINCT c.load_date)))
                AS files_per_observed_day,
            min(c.first_load_time)::TIMESTAMPTZ AS first_load_time,
            max(c.last_load_time)::TIMESTAMPTZ AS last_load_time,
            'copy_history' AS detection_method,
            'high' AS confidence,
            sum(c.n_files)::BIGINT AS supporting_event_count,
            any_value(r.actual_window_start)::TIMESTAMPTZ AS window_start,
            any_value(r.actual_window_end)::TIMESTAMPTZ AS window_end,
            max(c.last_load_time)::TIMESTAMPTZ AS last_observed_at,
            any_value(r.status) AS copy_history_extract_status,
            '{_INGESTION_NOTE}' AS note
        FROM raw.copy_history c
        LEFT JOIN meta.extract_runs r
            ON r.collection_id = c.collection_id AND r.extractor = 'copy_history'
        -- outcome rows: only successful loads are writes. Failed/skipped
        -- attempts stay in raw.copy_history as evidence but must never
        -- produce days_with_writes or supporting events (review, 2026-08-19).
        WHERE c.load_status = 'loaded'
        GROUP BY c.collection_id, c.table_catalog, c.table_schema,
                 c.table_name, c.load_method
    """)


# ── M3c: facts over the server-side workload aggregates (decision 16) ──────
# All source extracts run their GROUP BY inside Snowflake; these builders
# materialize the results with the coverage contract attached. Same shape
# rules as sizing: the relation always exists, empty when evidence is
# missing, and meta.extract_runs says why.

_CONCURRENCY_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.concurrency_profile;
CREATE TABLE report.concurrency_profile (
    collection_id UUID, warehouse_name VARCHAR, hour_start TIMESTAMPTZ,
    peak_concurrent_queries BIGINT, avg_concurrent_queries DOUBLE,
    busy_seconds DOUBLE, concurrency_extract_status VARCHAR
);
"""


def _build_concurrency_profile(con: duckdb.DuckDBPyConnection) -> None:
    # Exact hourly peaks from the server-side event sweep over exact
    # timestamps, hour-complete via boundary carriers. Early-window peaks
    # are floors (queries started before the window contribute no event);
    # the caveat lives in the extract SQL and travels via provenance.
    con.execute(_CONCURRENCY_DDL)
    if not table_exists(con, "raw", "query_concurrency"):
        return
    con.execute("""
        INSERT INTO report.concurrency_profile BY NAME
        SELECT
            c.collection_id AS collection_id,
            c.warehouse_name AS warehouse_name,
            c.hour_start::TIMESTAMPTZ AS hour_start,
            c.peak_concurrent_queries::BIGINT AS peak_concurrent_queries,
            c.avg_concurrent_queries::DOUBLE AS avg_concurrent_queries,
            c.busy_seconds::DOUBLE AS busy_seconds,
            r.status AS concurrency_extract_status
        FROM raw.query_concurrency c
        LEFT JOIN meta.extract_runs r
            ON r.collection_id = c.collection_id
            AND r.extractor = 'query_concurrency'
    """)


_FINGERPRINTS_DDL = """
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
    """One row per (tool, detection method), unioned from three evidence
    sources with the spec's provenance contract. Confidence reflects the
    evidence class: client_application/login are Snowflake-reported software
    identity ('high'); query_tag mapping is pattern inference ('medium')."""
    con.execute(_FINGERPRINTS_DDL)
    if table_exists(con, "raw", "client_app_fingerprints"):
        con.execute("""
            INSERT INTO report.tool_fingerprints BY NAME
            SELECT
                f.collection_id AS collection_id,
                coalesce(f.client_application_id, '(unknown client)') AS tool,
                'client_application' AS detection_method,
                'high' AS confidence,
                sum(f.n_queries)::BIGINT AS n_events,
                max(f.n_distinct_users)::BIGINT AS n_distinct_users,
                sum(f.sum_elapsed_ms)::BIGINT AS sum_elapsed_ms,
                min(f.first_seen)::TIMESTAMPTZ AS first_seen,
                max(f.last_seen)::TIMESTAMPTZ AS last_seen,
                any_value(r.actual_window_start)::TIMESTAMPTZ AS window_start,
                any_value(r.actual_window_end)::TIMESTAMPTZ AS window_end,
                any_value(r.status) AS extract_status,
                'n_distinct_users is a per-day lower bound' AS note
            FROM raw.client_app_fingerprints f
            LEFT JOIN meta.extract_runs r
                ON r.collection_id = f.collection_id
                AND r.extractor = 'client_app_fingerprints'
            GROUP BY f.collection_id, tool
        """)
    if table_exists(con, "raw", "query_tag_fingerprints"):
        con.execute("""
            INSERT INTO report.tool_fingerprints BY NAME
            SELECT
                f.collection_id AS collection_id,
                f.query_tag_tool AS tool,
                'query_tag' AS detection_method,
                'medium' AS confidence,
                sum(f.n_queries)::BIGINT AS n_events,
                max(f.n_distinct_users)::BIGINT AS n_distinct_users,
                sum(f.sum_elapsed_ms)::BIGINT AS sum_elapsed_ms,
                min(f.first_seen)::TIMESTAMPTZ AS first_seen,
                max(f.last_seen)::TIMESTAMPTZ AS last_seen,
                any_value(r.actual_window_start)::TIMESTAMPTZ AS window_start,
                any_value(r.actual_window_end)::TIMESTAMPTZ AS window_end,
                any_value(r.status) AS extract_status,
                'tag-pattern inference; n_distinct_users is a per-day lower bound' AS note
            FROM raw.query_tag_fingerprints f
            LEFT JOIN meta.extract_runs r
                ON r.collection_id = f.collection_id
                AND r.extractor = 'query_tag_fingerprints'
            GROUP BY f.collection_id, f.query_tag_tool
        """)
    if table_exists(con, "raw", "login_history"):
        con.execute("""
            INSERT INTO report.tool_fingerprints BY NAME
            SELECT
                l.collection_id AS collection_id,
                coalesce(l.client_type, '(unknown client)') AS tool,
                'login_history' AS detection_method,
                'high' AS confidence,
                sum(l.n_logins)::BIGINT AS n_events,
                count(DISTINCT l.user_name)::BIGINT AS n_distinct_users,
                CAST(NULL AS BIGINT) AS sum_elapsed_ms,
                min(l.first_seen)::TIMESTAMPTZ AS first_seen,
                max(l.last_seen)::TIMESTAMPTZ AS last_seen,
                any_value(r.actual_window_start)::TIMESTAMPTZ AS window_start,
                any_value(r.actual_window_end)::TIMESTAMPTZ AS window_end,
                any_value(r.status) AS extract_status,
                'connection evidence only; says nothing about query volume' AS note
            FROM raw.login_history l
            LEFT JOIN meta.extract_runs r
                ON r.collection_id = l.collection_id
                AND r.extractor = 'login_history'
            GROUP BY l.collection_id, tool
        """)


_SHAPES_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.query_shapes;
CREATE TABLE report.query_shapes (
    collection_id UUID, shape_key VARCHAR,
    query_parameterized_hash_version VARCHAR, query_type VARCHAR,
    warehouse_name VARCHAR, n_shapes BIGINT, n_queries BIGINT,
    sum_elapsed_ms BIGINT, sum_bytes_scanned BIGINT,
    first_seen TIMESTAMPTZ, last_seen TIMESTAMPTZ,
    shapes_extract_status VARCHAR
);
"""


def _build_query_shapes(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SHAPES_DDL)
    if not table_exists(con, "raw", "query_shapes"):
        return
    con.execute("""
        INSERT INTO report.query_shapes BY NAME
        SELECT
            s.collection_id AS collection_id,
            s.shape_key AS shape_key,
            s.query_parameterized_hash_version::VARCHAR
                AS query_parameterized_hash_version,
            s.query_type AS query_type,
            s.warehouse_name AS warehouse_name,
            s.n_shapes::BIGINT AS n_shapes,
            s.n_queries::BIGINT AS n_queries,
            s.sum_elapsed_ms::BIGINT AS sum_elapsed_ms,
            s.sum_bytes_scanned::BIGINT AS sum_bytes_scanned,
            s.first_seen::TIMESTAMPTZ AS first_seen,
            s.last_seen::TIMESTAMPTZ AS last_seen,
            r.status AS shapes_extract_status
        FROM raw.query_shapes s
        LEFT JOIN meta.extract_runs r
            ON r.collection_id = s.collection_id
            AND r.extractor = 'query_shapes'
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
    rollup_extract_status VARCHAR
);
"""


def _build_workload_rollup(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_ROLLUP_DDL)
    if not table_exists(con, "raw", "query_workload_rollup"):
        return
    con.execute("""
        INSERT INTO report.workload_rollup BY NAME
        SELECT
            w.collection_id AS collection_id,
            w.warehouse_name AS warehouse_name,
            w.query_type AS query_type,
            w.usage_date::DATE AS usage_date,
            w.n_queries::BIGINT AS n_queries,
            w.n_succeeded::BIGINT AS n_succeeded,
            w.sum_elapsed_ms::BIGINT AS sum_elapsed_ms,
            w.p95_elapsed_ms::DOUBLE AS p95_elapsed_ms,
            w.sum_bytes_scanned::BIGINT AS sum_bytes_scanned,
            w.p50_bytes_scanned::DOUBLE AS p50_bytes_scanned,
            w.p95_bytes_scanned::DOUBLE AS p95_bytes_scanned,
            w.n_spilled_local::BIGINT AS n_spilled_local,
            w.n_spilled_remote::BIGINT AS n_spilled_remote,
            w.sum_bytes_spilled_local::BIGINT AS sum_bytes_spilled_local,
            w.sum_bytes_spilled_remote::BIGINT AS sum_bytes_spilled_remote,
            w.sum_queued_overload_ms::BIGINT AS sum_queued_overload_ms,
            r.status AS rollup_extract_status
        FROM raw.query_workload_rollup w
        LEFT JOIN meta.extract_runs r
            ON r.collection_id = w.collection_id
            AND r.extractor = 'query_workload_rollup'
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
    ("flatten", "n_flatten"),
    ("colon_path_variant_access", "n_colon_path"),
    ("pivot_unpivot", "n_pivot_unpivot"),
    ("connect_by", "n_connect_by"),
    ("match_recognize", "n_match_recognize"),
    ("time_travel_at_before", "n_time_travel"),
    ("result_scan", "n_result_scan"),
    ("identifier_fn", "n_identifier_fn"),
]

_DIALECT_NOTE = (
    "server-side pattern heuristic over workload SQL (query text never "
    "collected); counts are frequency signals, not parse results"
)


def _build_dialect_constructs(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_DIALECT_DDL)
    if not table_exists(con, "raw", "query_dialect_constructs"):
        return
    for construct, column in _DIALECT_COLUMNS:
        con.execute(f"""
            INSERT INTO report.dialect_constructs BY NAME
            SELECT
                d.collection_id AS collection_id,
                '{construct}' AS construct,
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


#: Run in this order by the neutral report layer after feature_inventory.
FACT_BUILDERS = (
    _build_sizing,
    _build_spend_profile,
    _build_workload_profile,
    _build_ingestion_inventory,
    _build_concurrency_profile,
    _build_tool_fingerprints,
    _build_query_shapes,
    _build_workload_rollup,
    _build_dialect_constructs,
)
