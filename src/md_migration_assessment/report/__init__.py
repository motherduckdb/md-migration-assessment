"""The factual report layer (public half of the assessment, spec §5).

``build_report`` materializes ``report.feature_inventory`` and
``report.sizing`` for every collection in the database. Facts only — no
compatibility ratings, no effort scores; those are applied by the internal
overlay.

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
    return summary


def _build_sizing(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("DROP TABLE IF EXISTS report.sizing")
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
        CREATE TABLE report.sizing AS
        SELECT
            t.collection_id,
            t.table_catalog, t.table_schema, t.table_name, t.table_type,
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
