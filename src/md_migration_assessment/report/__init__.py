"""The factual report layer (public half of the assessment, spec §5).

``build_report`` materializes ``report.feature_inventory`` for every
collection in the database, then runs the source adapter's fact builders
(sizing, workload, spend, ... — each adapter owns its list). Facts only —
no compatibility ratings, no effort scores; those are applied by the
internal overlay.

``report.feature_inventory`` is the cross-source contract: its shape and
its observation-status semantics are the same for every adapter. The other
``report.*`` relations are adapter-owned until a second source shows which
columns are genuinely common.

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

from .. import db
from ..sources import get_adapter
from .facts import table_exists

__all__ = ["build_report", "table_exists"]

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


def build_report(con: duckdb.DuckDBPyConnection) -> dict:
    """(Re)build report.* for all collections. Idempotent. Returns a summary."""
    collections = db.collection_kinds(con)
    kinds = {kind for _, kind, _ in collections}
    if len(kinds) > 1:
        raise ValueError(
            f"cannot build a report over mixed source kinds {sorted(kinds)}: "
            "collect each source into its own file"
        )
    adapter = get_adapter(next(iter(kinds))) if kinds else None
    for cid, _, raw_version in collections:
        if raw_version != adapter.raw_schema_version:
            raise ValueError(
                f"collection {cid} has raw schema v{raw_version}; this tool "
                f"builds {adapter.name} reports for v{adapter.raw_schema_version}. "
                "Re-collect with the current version (explicit migrations are "
                "not provided pre-1.0)."
            )

    con.execute(_FEATURE_DDL)
    summary = {"collections": len(collections), "features": 0, "unknown": 0}

    for cid, _, _ in collections:
        runs = {
            r[0]: {"status": r[1], "source_used": r[2]}
            for r in con.execute(
                "SELECT extractor, status, source_used FROM meta.extract_runs "
                "WHERE collection_id = ?",
                [cid],
            ).fetchall()
        }
        for sig in adapter.signals:
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
            else:  # unavailable | failed | interrupted
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
        for planned in adapter.planned_signals:
            con.execute(
                "INSERT INTO report.feature_inventory VALUES "
                "(?, ?, ?, 'unknown', NULL, [], '(not implemented)', NULL, NULL, ?)",
                [cid, planned.category, planned.name,
                 f"signal not implemented in this version: {planned.reason}"],
            )
            summary["features"] += 1
            summary["unknown"] += 1

    if adapter is not None:
        for build in adapter.fact_builders:
            build(con)
    return summary
