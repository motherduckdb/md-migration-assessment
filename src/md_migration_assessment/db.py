"""Output database handling: creation, meta schema, collection identity.

Contracts (spec §2/§3):

- ``meta.collections`` is an immutable record of what a collection run was:
  identity, versions, profile, scope, actual windows, execution mode, privacy
  settings. Rows are inserted once and never updated except to stamp
  ``finished_at``.
- ``meta.extract_runs`` records one row per extractor per collection — not
  only failures. Missing evidence must never read as an observed zero, so the
  row carries status, source used, scope/window actually covered, and error
  detail. ``meta.gaps`` is a convenience view over incomplete runs.
- The output file is created with mode 0600.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import duckdb

from . import META_SCHEMA_VERSION, RAW_SCHEMA_VERSION, __version__

# Anything scheme-like: md:, MD:, motherduck:, s3://, duckdb://, http:, ...
# DuckDB resolves several of these to remote systems (verified live:
# connect('MD:x') and connect('motherduck:x') both reach MotherDuck), so the
# only safe rule is an allowlist: a local path is one with NO scheme prefix.
# Single-letter prefixes followed by a path separator are Windows drive
# letters, not schemes.
_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")


def require_local_path(path: str, context: str) -> None:
    """The single definition of 'local file path' for every privacy-motivated
    guard in this tool — collection output and handoff alike must not drift
    apart on what counts as remote."""
    p = path.strip()
    if _SCHEME_RE.match(p) and not _DRIVE_RE.match(p):
        raise ValueError(
            f"{context} must be a plain local file path (got {path!r}); "
            "sharing or uploading to MotherDuck or remote storage is a "
            "separate, explicit upload operation"
        )


#: Extractor run statuses (spec §2). Values are stored as VARCHAR.
#: 'interrupted' = the user stopped the collection (Ctrl+C) before or during
#: this extractor; always retryable via --resume, and like every incomplete
#: status it reads as missing evidence, never as an observed zero.
STATUSES = (
    "complete", "partial", "unavailable", "failed", "not_requested",
    "interrupted",
)

_META_DDL = """
CREATE SCHEMA IF NOT EXISTS raw;
CREATE SCHEMA IF NOT EXISTS meta;

CREATE TABLE IF NOT EXISTS meta.collections (
    collection_id        UUID PRIMARY KEY,
    tool_version         VARCHAR NOT NULL,
    raw_schema_version   INTEGER NOT NULL,
    meta_schema_version  INTEGER NOT NULL,
    profile              VARCHAR NOT NULL,
    scope                JSON,               -- list of "DB" / "DB.SCHEMA", null = account-wide
    mode                 VARCHAR NOT NULL,   -- 'local' | 'managed'
    query_text_mode      VARCHAR NOT NULL,   -- always 'never_collected' (decision 16:
                                             -- workload query text is never fetched)
    history_days         INTEGER NOT NULL,
    snowflake_account    VARCHAR,
    snowflake_version    VARCHAR,
    snowflake_region     VARCHAR,
    snowflake_edition    VARCHAR,            -- nullable: not directly exposed by Snowflake
    started_at           TIMESTAMPTZ NOT NULL,
    finished_at          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS meta.extract_runs (
    collection_id        UUID NOT NULL,
    extractor            VARCHAR NOT NULL,
    extractor_version    VARCHAR NOT NULL,   -- content hash of the extract SQL
    target_table         VARCHAR NOT NULL,
    status               VARCHAR NOT NULL,   -- complete|partial|unavailable|failed|not_requested
    source_used          VARCHAR,            -- 'account_usage' | 'information_schema' | null
    requested_scope      JSON,
    actual_scope         JSON,               -- e.g. databases actually covered by a fallback walk
    requested_window_days INTEGER,
    actual_window_start  TIMESTAMPTZ,
    actual_window_end    TIMESTAMPTZ,
    rows_written         BIGINT,
    required_privilege   VARCHAR,
    min_edition          VARCHAR,
    error_category       VARCHAR,            -- 'privilege' | 'missing_object' | 'error' | null
    error_detail         VARCHAR,            -- human explanation
    retryable            BOOLEAN,
    started_at           TIMESTAMPTZ NOT NULL,
    finished_at          TIMESTAMPTZ NOT NULL
);

CREATE OR REPLACE VIEW meta.gaps AS
SELECT collection_id, extractor, status, source_used,
       error_category, error_detail, retryable
FROM meta.extract_runs
WHERE status IN ('partial', 'unavailable', 'failed', 'interrupted');
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def open_output(path: str) -> duckdb.DuckDBPyConnection:
    """Open (creating if needed) the local output database with mode 0600.

    Local mode never touches MotherDuck: reject md: paths outright so a
    misconfiguration cannot silently turn a private collection into an upload.
    """
    require_local_path(path, "local collection output")
    # The 0600 guarantee must cover DuckDB's sidecar files too (<path>.wal,
    # temp files), and those are created lazily by later writes — a chmod of
    # the main file alone leaves a world-readable WAL behind after a crash.
    # A restrictive umask makes every file this process creates private by
    # construction.
    os.umask(0o077)
    con = duckdb.connect(path)
    os.chmod(path, 0o600)
    wal = path + ".wal"
    if os.path.exists(wal):
        os.chmod(wal, 0o600)
    con.execute(_META_DDL)
    return con


@dataclass
class Collection:
    """Identity of one collection run, mirrored in meta.collections."""

    profile: str
    mode: str = "local"
    scope: list[str] | None = None
    query_text_mode: str = "never_collected"
    history_days: int = 30
    snowflake_account: str | None = None
    snowflake_version: str | None = None
    snowflake_region: str | None = None
    snowflake_edition: str | None = None
    collection_id: uuid.UUID = field(default_factory=uuid.uuid4)
    started_at: datetime = field(default_factory=utcnow)


def begin_collection(con: duckdb.DuckDBPyConnection, coll: Collection) -> None:
    con.execute(
        """
        INSERT INTO meta.collections (
            collection_id, tool_version, raw_schema_version, meta_schema_version,
            profile, scope, mode, query_text_mode, history_days,
            snowflake_account, snowflake_version, snowflake_region, snowflake_edition,
            started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(coll.collection_id),
            __version__,
            RAW_SCHEMA_VERSION,
            META_SCHEMA_VERSION,
            coll.profile,
            json.dumps(coll.scope) if coll.scope is not None else None,
            coll.mode,
            coll.query_text_mode,
            coll.history_days,
            coll.snowflake_account,
            coll.snowflake_version,
            coll.snowflake_region,
            coll.snowflake_edition,
            coll.started_at,
        ],
    )


def finish_collection(con: duckdb.DuckDBPyConnection, coll: Collection) -> None:
    con.execute(
        "UPDATE meta.collections SET finished_at = ? WHERE collection_id = ?",
        [utcnow(), str(coll.collection_id)],
    )


def reopen_collection(con: duckdb.DuckDBPyConnection, coll: Collection) -> None:
    """Clear finished_at when a resume starts re-running extractors: while
    work is in flight the collection must read as unfinished, so an
    interrupted retry cannot leave a 'finished' collection whose extractors
    say interrupted. finish_collection re-stamps it on success."""
    con.execute(
        "UPDATE meta.collections SET finished_at = NULL WHERE collection_id = ?",
        [str(coll.collection_id)],
    )


def load_collection(con: duckdb.DuckDBPyConnection) -> tuple[Collection, int]:
    """Load the database's single collection for --resume.

    Returns (collection, stored raw_schema_version). The stored parameters
    are authoritative on resume — profile, scope, and history window come
    from here, never from fresh CLI flags.
    """
    rows = con.execute(
        """
        SELECT collection_id, profile, mode, scope, query_text_mode,
               history_days, snowflake_account, snowflake_version,
               snowflake_region, snowflake_edition, started_at,
               raw_schema_version
        FROM meta.collections
        """
    ).fetchall()
    if not rows:
        raise ValueError("nothing to resume: this database contains no collection")
    if len(rows) > 1:
        raise ValueError(
            "cannot resume: multiple collections in one file (unsupported)"
        )
    (cid, profile, mode, scope, qtm, days, acct, ver, region, edition,
     started, raw_version) = rows[0]
    coll = Collection(
        profile=profile,
        mode=mode,
        scope=json.loads(scope) if scope is not None else None,
        query_text_mode=qtm,
        history_days=days,
        snowflake_account=acct,
        snowflake_version=ver,
        snowflake_region=region,
        snowflake_edition=edition,
        collection_id=uuid.UUID(str(cid)),
        started_at=started,
    )
    return coll, raw_version


def delete_extract_run(
    con: duckdb.DuckDBPyConnection, coll: Collection, extractor: str
) -> None:
    """Remove an extractor's coverage row before a resume re-runs it, so the
    collection keeps exactly one authoritative row per extractor."""
    con.execute(
        "DELETE FROM meta.extract_runs WHERE collection_id = ? AND extractor = ?",
        [str(coll.collection_id), extractor],
    )


@dataclass
class ExtractRun:
    """One extractor's outcome, mirrored in meta.extract_runs."""

    extractor: str
    extractor_version: str
    target_table: str
    status: str
    started_at: datetime
    source_used: str | None = None
    requested_scope: list[str] | None = None
    actual_scope: list[str] | None = None
    requested_window_days: int | None = None
    actual_window_start: datetime | None = None
    actual_window_end: datetime | None = None
    rows_written: int | None = None
    required_privilege: str | None = None
    min_edition: str | None = None
    error_category: str | None = None
    error_detail: str | None = None
    retryable: bool | None = None


def record_extract_run(
    con: duckdb.DuckDBPyConnection, coll: Collection, run: ExtractRun
) -> None:
    assert run.status in STATUSES, f"invalid status {run.status!r}"
    con.execute(
        """
        INSERT INTO meta.extract_runs (
            collection_id, extractor, extractor_version, target_table, status,
            source_used, requested_scope, actual_scope,
            requested_window_days, actual_window_start, actual_window_end,
            rows_written, required_privilege, min_edition,
            error_category, error_detail, retryable, started_at, finished_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            str(coll.collection_id),
            run.extractor,
            run.extractor_version,
            run.target_table,
            run.status,
            run.source_used,
            json.dumps(run.requested_scope) if run.requested_scope is not None else None,
            json.dumps(run.actual_scope) if run.actual_scope is not None else None,
            run.requested_window_days,
            run.actual_window_start,
            run.actual_window_end,
            run.rows_written,
            run.required_privilege,
            run.min_edition,
            run.error_category,
            run.error_detail,
            run.retryable,
            run.started_at,
            utcnow(),
        ],
    )
