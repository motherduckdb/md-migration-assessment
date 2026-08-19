"""The factual report layer (public half of the assessment, spec §5).

``build_report`` materializes ``report.feature_inventory``, ``report.sizing``,
and the M3b workload facts (``report.spend_profile``,
``report.workload_profile``, ``report.ingestion_inventory``) for every
collection in the database. Facts only — no compatibility ratings, no effort
scores; those are applied by the internal overlay.

Observation-status contract (spec §5): every feature row states how it was
observed, and missing evidence is never presented as zero:

- ``observed``       — the source extract succeeded and the count is > 0
                       (under ``partial`` coverage the count is a lower bound;
                       the note says so)
- ``observed_zero``  — the source extract fully succeeded and found nothing
- ``unknown``        — the source extract was unavailable/failed/partial-empty,
                       or the probe itself errored
- ``not_requested``  — the collection profile did not include the source
"""

from __future__ import annotations

import duckdb

from .. import RAW_SCHEMA_VERSION
from .signals import PLANNED_SIGNALS, SIGNALS

_FEATURE_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.feature_inventory;
CREATE TABLE report.feature_inventory (
    collection_id      UUID NOT NULL,
    category           VARCHAR NOT NULL,
    feature            VARCHAR NOT NULL,
    observation_status VARCHAR NOT NULL,  -- observed|observed_zero|unknown|not_requested
    count              BIGINT,            -- NULL unless observed/observed_zero
    sample_objects     VARCHAR[],
    source_extractor   VARCHAR NOT NULL,
    extract_status     VARCHAR,
    source_used        VARCHAR,
    note               VARCHAR
);
"""


def _table_exists(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        ).fetchone()[0]
    )


def build_report(con: duckdb.DuckDBPyConnection) -> dict:
    """(Re)build report.* for all collections. Idempotent. Returns a summary."""
    collections = con.execute(
        "SELECT collection_id, raw_schema_version FROM meta.collections"
    ).fetchall()
    for cid, raw_version in collections:
        if raw_version != RAW_SCHEMA_VERSION:
            raise ValueError(
                f"collection {cid} has raw schema v{raw_version}; this tool "
                f"builds reports for v{RAW_SCHEMA_VERSION}. Re-collect with the "
                "current version (explicit migrations are not provided pre-1.0)."
            )

    con.execute(_FEATURE_DDL)
    summary = {"collections": len(collections), "features": 0, "unknown": 0}

    for (cid, _) in collections:
        cid = str(cid)
        runs = {
            r[0]: {"status": r[1], "source_used": r[2]}
            for r in con.execute(
                "SELECT extractor, status, source_used FROM meta.extract_runs "
                "WHERE collection_id = ?",
                [cid],
            ).fetchall()
        }
        for sig in SIGNALS:
            run = runs.get(sig.source_extractor)
            count: int | None = None
            samples: list[str] = []
            note: str | None = None

            if run is None:
                obs = "unknown"
                note = "source extractor not present in this collection"
            elif run["status"] == "not_requested":
                obs = "not_requested"
            elif run["status"] in ("complete", "partial"):
                try:
                    n, sample_objects = con.execute(
                        sig.sql.replace("{cid}", cid)
                    ).fetchone()
                    count = int(n)
                    samples = [str(s) for s in (sample_objects or [])]
                    if count > 0:
                        obs = "observed"
                        if run["status"] == "partial":
                            note = "source coverage partial — count is a lower bound"
                    elif run["status"] == "complete":
                        obs = "observed_zero"
                    else:
                        obs = "unknown"
                        count = None
                        note = "source coverage partial and nothing observed"
                except Exception as exc:  # noqa: BLE001 — a broken probe is unknown, not zero
                    obs = "unknown"
                    count = None
                    note = f"probe failed: {exc}"
            else:  # unavailable | failed
                obs = "unknown"
                note = f"source extract {run['status']}"

            con.execute(
                "INSERT INTO report.feature_inventory VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    cid,
                    sig.category,
                    sig.name,
                    obs,
                    count,
                    samples,
                    sig.source_extractor,
                    run["status"] if run else None,
                    run["source_used"] if run else None,
                    note,
                ],
            )
            summary["features"] += 1
            if obs == "unknown":
                summary["unknown"] += 1

        # Taxonomy entries with no probe yet: visible unknowns, never silent
        # omissions — the inventory must not look complete when it is not.
        for planned in PLANNED_SIGNALS:
            con.execute(
                "INSERT INTO report.feature_inventory VALUES "
                "(?, ?, ?, 'unknown', NULL, [], '(not implemented)', NULL, NULL, ?)",
                [cid, planned.category, planned.name,
                 f"signal not implemented in this version: {planned.reason}"],
            )
            summary["features"] += 1
            summary["unknown"] += 1

    _build_sizing(con)
    _build_spend_profile(con)
    _build_workload_profile(con)
    _build_ingestion_inventory(con)
    return summary


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
    if not _table_exists(con, "raw", "tables"):
        return
    storage_join = ""
    storage_cols = (
        "CAST(NULL AS BIGINT) AS active_bytes, CAST(NULL AS BIGINT) AS time_travel_bytes, "
        "CAST(NULL AS BIGINT) AS failsafe_bytes, CAST(NULL AS BIGINT) AS retained_for_clone_bytes"
    )
    if _table_exists(con, "raw", "table_storage_metrics"):
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
    if not _table_exists(con, "raw", "warehouse_metering_history"):
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
    if not _table_exists(con, "raw", "warehouse_load_history"):
        return
    credits_col = "CAST(NULL AS DOUBLE) AS credits_used"
    metering_join = ""
    if _table_exists(con, "raw", "warehouse_metering_history"):
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
    if not _table_exists(con, "raw", "copy_history"):
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
