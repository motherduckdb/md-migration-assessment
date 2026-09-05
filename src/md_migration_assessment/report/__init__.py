"""The factual report layer.

``build_report`` materializes ``report.feature_inventory`` for every
collection in the database, then runs the source adapter's fact builders
(sizing, workload, spend, ... — each adapter owns its list). It produces facts
only, with no compatibility ratings or migration-effort scores.

``report.feature_inventory`` is the cross-source contract: its shape and
its observation-status semantics are the same for every adapter. The other
``report.*`` relations are adapter-owned until a second source shows which
columns are genuinely common.

Observation-status contract: every feature row states how it was
observed, and missing evidence is never presented as zero:

- ``observed``       — the source extract succeeded and the count is > 0
                       (under ``partial`` coverage the count is a lower bound;
                       the note says so)
- ``observed_zero``  — the source extract fully succeeded and found nothing
- ``unknown``        — the source extract was unavailable/failed/partial-empty,
                       or the probe itself errored
- ``not_requested``  — the collection profile did not include the source

Visibility-bound sources (strategies flagged ``visibility_bound``, e.g. a
listing that shows only objects the collecting role can see) cannot prove
absence, so the contract applies structurally, not in prose:

- a positive count is ``observed`` with ``lower_bound = true`` (the column
  is also true under ``partial`` coverage);
- a zero is ``unknown`` with ``count = NULL`` — a role-visibility gap is
  missing evidence, and missing evidence is never presented as zero, even
  though many deployments genuinely have none of these objects. The note
  says how to resolve it (collect as a role that can see the objects).
"""

from __future__ import annotations

import duckdb

from .. import __version__, db
from ..db import utcnow
from ..sources import get_adapter
from .facts import table_exists

__all__ = [
    "REPORT_SCHEMA_VERSION",
    "build_report",
    "check_report_version",
    "table_exists",
    "visibility_bound_labels",
]

#: Version of the report.* shapes. Bump on any change to a cross-source
#: relation's columns. Stamped into report.schema_version by build_report and
#: checked by every reader (CLI report, handoff) before selecting columns.
#: v1: pre-versioning (v0.1.2 and earlier; no schema_version table).
#: v2: feature_inventory += lower_bound, unknown_reason.
REPORT_SCHEMA_VERSION = 2

_VERSION_DDL = """
CREATE TABLE report.schema_version (
    report_schema_version INTEGER NOT NULL,
    tool_version          VARCHAR NOT NULL,
    built_at              TIMESTAMPTZ NOT NULL
);
"""


def _join(*parts: str | None) -> str | None:
    kept = [p for p in parts if p]
    return "; ".join(kept) if kept else None


def check_report_version(con: duckdb.DuckDBPyConnection, db_path: str = "<db>") -> bool:
    """Refuse to read report.* built under a different report schema.

    Returns False when no report has been built at all (readers then skip the
    report sections), True when the stamped version matches, and raises with a
    rebuild instruction otherwise. A report.feature_inventory without a
    schema_version table is a v1 report (v0.1.2 and earlier).
    """
    if not table_exists(con, "report", "feature_inventory"):
        return False
    stored = 1
    if table_exists(con, "report", "schema_version"):
        row = con.execute(
            "SELECT report_schema_version FROM report.schema_version"
        ).fetchone()
        stored = row[0] if row else 1
    if stored != REPORT_SCHEMA_VERSION:
        raise ValueError(
            f"report.* in this database was built with report schema v{stored}; "
            f"this tool reads v{REPORT_SCHEMA_VERSION}. The raw evidence is "
            f"unaffected — rebuild the report with: md-assess assess --db {db_path}"
        )
    return True


def visibility_bound_labels(adapter) -> dict[str, set[str]]:
    """extractor name -> the ``source_used`` labels of its strategies that
    list only what the collecting role can see. Derived from the manifest,
    so report and CLI agree without matching on note text."""
    return {
        ex.name: {s.label for s in ex.sources if getattr(s, "visibility_bound", False)}
        for ex in adapter.extractors
    }


VISIBILITY_UNKNOWN_NOTE = (
    "no objects visible to the collecting role: the source lists only "
    "objects the role has a privilege on, so absence cannot be confirmed; "
    "collect as a role that can see these objects (e.g. one with MANAGE "
    "GRANTS) to resolve"
)
VISIBILITY_LOWER_BOUND_NOTE = (
    "source lists only objects visible to the collecting role — count is a "
    "lower bound"
)

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
    -- true when the count cannot be exhaustive: partial coverage, or a
    -- source that lists only what the collecting role can see
    lower_bound        BOOLEAN NOT NULL,
    note               VARCHAR,
    -- why observation_status is 'unknown' (NULL otherwise):
    --   extract_unavailable | extract_failed | extract_interrupted |
    --   partial_nothing_observed | not_visible | probe_failed |
    --   not_implemented | extractor_missing
    unknown_reason     VARCHAR
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

    # report.* is tool-owned and rebuilt from scratch: a relation an older
    # adapter version produced must not survive a rebuild that no longer
    # declares it (handoff copies every report table wholesale).
    con.execute("DROP SCHEMA IF EXISTS report CASCADE")
    con.execute(_FEATURE_DDL)
    summary = {"collections": len(collections), "features": 0, "unknown": 0}

    bound_labels = visibility_bound_labels(adapter) if adapter else {}

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
            lower_bound = False
            reason: str | None = None

            if run is None:
                obs = "unknown"
                note = "source extractor not present in this collection"
                reason = "extractor_missing"
            elif run["status"] == "not_requested":
                obs = "not_requested"
            elif run["status"] in ("complete", "partial"):
                try:
                    n, sample_objects = con.execute(
                        sig.sql.replace("{cid}", cid)
                    ).fetchone()
                    count = int(n)
                    samples = [str(s) for s in (sample_objects or [])]
                    bound = run["source_used"] in bound_labels.get(
                        sig.source_extractor, set()
                    )
                    if count > 0:
                        obs = "observed"
                        if run["status"] == "partial":
                            note = "source coverage partial — count is a lower bound"
                            lower_bound = True
                        if bound:
                            note = _join(note, VISIBILITY_LOWER_BOUND_NOTE)
                            lower_bound = True
                    elif run["status"] == "complete" and not bound:
                        obs = "observed_zero"
                    elif run["status"] == "complete":
                        # zero under role visibility is missing evidence
                        obs = "unknown"
                        count = None
                        note = VISIBILITY_UNKNOWN_NOTE
                        reason = "not_visible"
                    else:
                        obs = "unknown"
                        count = None
                        note = "source coverage partial and nothing observed"
                        reason = "partial_nothing_observed"
                except Exception as exc:  # noqa: BLE001 — a broken probe is unknown, not zero
                    obs = "unknown"
                    count = None
                    note = f"probe failed: {exc}"
                    reason = "probe_failed"
            else:  # unavailable | failed | interrupted
                obs = "unknown"
                note = f"source extract {run['status']}"
                reason = f"extract_{run['status']}"

            con.execute(
                "INSERT INTO report.feature_inventory VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    lower_bound,
                    note,
                    reason,
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
                "(?, ?, ?, 'unknown', NULL, [], '(not implemented)', NULL, NULL, false, ?, "
                "'not_implemented')",
                [cid, planned.category, planned.name,
                 f"signal not implemented in this version: {planned.reason}"],
            )
            summary["features"] += 1
            summary["unknown"] += 1

    if adapter is not None:
        for build in adapter.fact_builders:
            build(con)
    # Stamp LAST: a build that dies midway leaves no version row, and readers
    # then treat the report as stale and ask for a rebuild.
    con.execute(_VERSION_DDL)
    con.execute(
        "INSERT INTO report.schema_version VALUES (?, ?, ?)",
        [REPORT_SCHEMA_VERSION, __version__, utcnow()],
    )
    return summary
