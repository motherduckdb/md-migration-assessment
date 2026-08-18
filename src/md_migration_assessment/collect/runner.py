"""Collection orchestration.

Runs every extractor the profile requests, lands results in ``raw.*`` stamped
with the collection_id, and records one ``meta.extract_runs`` row per
extractor — success or not. A partial collection is always a valid output;
missing evidence is recorded, never silently omitted (spec §2).

Status semantics:

- ``not_requested``: the chosen profile does not include this extractor.
- ``complete``: the extract covered its full requested scope.
- ``partial``: an INFORMATION_SCHEMA fallback walk succeeded for some
  databases and failed for others; ``actual_scope`` lists coverage.
- ``unavailable``: the source view/privilege/edition is not accessible
  (classified from the Snowflake error), and no fallback succeeded.
- ``failed``: unexpected error; ``retryable`` is true.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Protocol

import duckdb
import pyarrow as pa

from .. import db
from ..db import Collection, ExtractRun, utcnow
from .manifest import EXTRACTORS, Extractor, Profile, extractor_version, load_sql
from .snowflake import SessionInfo

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")

# Snowflake error text that means "not visible to this role/edition" rather
# than a real failure. Matched on the message so tests need no connector import.
_UNAVAILABLE_RE = re.compile(
    r"does not exist or not authorized"
    r"|insufficient privileges"
    r"|not authorized"
    r"|unsupported feature",
    re.IGNORECASE,
)


class Source(Protocol):
    """What the runner needs from a data source (live Snowflake or a test fake)."""

    def reader(self, sql: str) -> pa.RecordBatchReader | pa.Table: ...

    def list_databases(self) -> list[str]: ...

    def session_info(self) -> SessionInfo: ...


@dataclass(frozen=True)
class Scope:
    """Parsed --scope entries: database names and database.schema pairs."""

    databases: frozenset[str]
    schemas: frozenset[tuple[str, str]]

    @classmethod
    def parse(cls, entries: Iterable[str] | None) -> "Scope | None":
        if not entries:
            return None
        dbs: set[str] = set()
        schemas: set[tuple[str, str]] = set()
        for entry in entries:
            parts = entry.split(".")
            if len(parts) not in (1, 2) or not all(_IDENTIFIER_RE.match(p) for p in parts):
                raise ValueError(
                    f"invalid --scope entry {entry!r}: use DB or DB.SCHEMA "
                    "(quoted/special-character identifiers are not supported yet)"
                )
            parts = [p.upper() for p in parts]
            if len(parts) == 1:
                dbs.add(parts[0])
            else:
                schemas.add((parts[0], parts[1]))
        return cls(databases=frozenset(dbs), schemas=frozenset(schemas))

    def all_databases(self) -> set[str]:
        return set(self.databases) | {d for d, _ in self.schemas}

    def as_list(self) -> list[str]:
        return sorted(self.databases) + sorted(f"{d}.{s}" for d, s in self.schemas)

    def predicate(self, scope_columns: dict[str, str]) -> str:
        """Render a SQL predicate over the extractor's scope columns, or ''."""
        db_col = scope_columns.get("database")
        schema_col = scope_columns.get("schema")
        if db_col is None:
            return ""
        parts: list[str] = []
        if self.databases:
            names = ", ".join(f"'{d}'" for d in sorted(self.databases))
            parts.append(f"{db_col} IN ({names})")
        if self.schemas:
            if schema_col is None:
                # Extract has no schema granularity: include the whole database.
                names = ", ".join(f"'{d}'" for d in sorted({d for d, _ in self.schemas}))
                parts.append(f"{db_col} IN ({names})")
            else:
                for d, s in sorted(self.schemas):
                    parts.append(f"({db_col} = '{d}' AND {schema_col} = '{s}')")
        return " OR ".join(parts)


def _render(sql: str, *, scope_pred: str, window_days: int | None, database: str | None = None) -> str:
    scope_filter = f"AND ({scope_pred})" if scope_pred else ""
    out = sql.replace("{scope_filter}", scope_filter)
    if window_days is not None:
        out = out.replace("{window_days}", str(int(window_days)))
    if database is not None:
        # Discovered database names come from Snowflake itself and may contain
        # spaces, punctuation, or mixed case — quote, never reject.
        quoted = '"' + database.replace('"', '""') + '"'
        literal = database.replace("'", "''")
        out = out.replace("{database}", quoted).replace("{database_literal}", literal)
    return out


def _classify(exc: Exception) -> str:
    return "unavailable" if _UNAVAILABLE_RE.search(str(exc)) else "failed"


class _Ingestor:
    """Lands Arrow data in raw.<target>, stamping collection_id on every row."""

    def __init__(self, con: duckdb.DuckDBPyConnection, coll: Collection, target: str):
        if not _IDENTIFIER_RE.match(target):
            raise ValueError(f"bad target table name {target!r}")
        self._con = con
        self._cid = str(coll.collection_id)
        self._target = target
        self._created = False
        self.rows = 0

    def ingest(self, data: pa.RecordBatchReader | pa.Table) -> None:
        self._con.register("_md_assess_batches", data)
        try:
            select = (
                f"SELECT '{self._cid}'::UUID AS collection_id, * FROM _md_assess_batches"
            )
            if not self._created:
                self._con.execute(
                    f'CREATE OR REPLACE TABLE raw."{self._target}" AS {select}'
                )
                self._created = True
            else:
                self._con.execute(f'INSERT INTO raw."{self._target}" {select}')
        finally:
            self._con.unregister("_md_assess_batches")

    def finish(self) -> int:
        if not self._created:
            return 0
        self.rows = self._con.execute(
            f'SELECT count(*) FROM raw."{self._target}" WHERE collection_id = ?',
            [self._cid],
        ).fetchone()[0]
        return self.rows


def run_collection(
    con: duckdb.DuckDBPyConnection,
    source: Source,
    *,
    profile: Profile,
    scope: Scope | None = None,
    history_days: int = 30,
    query_text_mode: str = "hashed",
    mode: str = "local",
) -> Collection:
    # Raw evidence is immutable: ingest uses CREATE OR REPLACE per extract, so
    # a second collection in the same file would silently destroy the first
    # collection's raw.* rows while meta.collections still listed both.
    existing = con.execute("SELECT count(*) FROM meta.collections").fetchone()[0]
    if existing:
        raise ValueError(
            "output database already contains a collection; write each "
            "collection to a new file"
        )

    info = source.session_info()
    coll = Collection(
        profile=profile.name.lower(),
        mode=mode,
        scope=scope.as_list() if scope else None,
        query_text_mode=query_text_mode,
        history_days=history_days,
        snowflake_account=info.account,
        snowflake_version=info.version,
        snowflake_region=info.region,
        snowflake_edition=info.edition,
    )
    db.begin_collection(con, coll)

    for ex in EXTRACTORS:
        run = _run_extractor(con, source, coll, ex, profile, scope)
        db.record_extract_run(con, coll, run)

    db.finish_collection(con, coll)
    return coll


def _base_run(ex: Extractor, scope: Scope | None) -> ExtractRun:
    return ExtractRun(
        extractor=ex.name,
        extractor_version=extractor_version(ex),
        target_table=ex.target_table,
        status="failed",  # overwritten below
        started_at=utcnow(),
        requested_scope=scope.as_list() if scope else None,
        requested_window_days=ex.window_days,
        required_privilege=ex.required_privilege,
        min_edition=ex.min_edition,
    )


def _run_extractor(
    con: duckdb.DuckDBPyConnection,
    source: Source,
    coll: Collection,
    ex: Extractor,
    profile: Profile,
    scope: Scope | None,
) -> ExtractRun:
    run = _base_run(ex, scope)

    if profile < ex.min_profile:
        run.status = "not_requested"
        run.error_detail = f"requires profile '{ex.min_profile.name.lower()}' or higher"
        return run

    scope_pred = scope.predicate(ex.scope_columns) if scope else ""

    # ACCOUNT_USAGE path — skipped entirely in lite (no ACCOUNT_USAGE grants).
    au_error: Exception | None = None
    if ex.account_usage_sql and profile >= Profile.STANDARD:
        try:
            sql = _render(
                load_sql("account_usage", ex.account_usage_sql),
                scope_pred=scope_pred,
                window_days=ex.window_days,
            )
            ing = _Ingestor(con, coll, ex.target_table)
            ing.ingest(source.reader(sql))
            run.rows_written = ing.finish()
            run.status = "complete"
            run.source_used = "account_usage"
            if ex.window_days is not None:
                run.actual_window_end = utcnow()
                run.actual_window_start = run.actual_window_end - timedelta(days=ex.window_days)
            return run
        except Exception as exc:  # noqa: BLE001 — every failure becomes coverage metadata
            au_error = exc
            if _classify(exc) == "failed":
                run.status = "failed"
                run.error_category = "error"
                run.error_detail = f"ACCOUNT_USAGE extract failed: {exc}"
                run.retryable = True
                return run
            # unavailable → fall through to INFORMATION_SCHEMA if we have it

    # INFORMATION_SCHEMA fallback: one walk per accessible (or scoped) database.
    if ex.info_schema_sql:
        try:
            databases = source.list_databases()
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error_category = "error"
            run.error_detail = f"could not enumerate databases: {exc}"
            run.retryable = True
            return run
        ing = _Ingestor(con, coll, ex.target_table)
        succeeded: list[str] = []
        failures: list[str] = []
        failure_kinds: set[str] = set()
        if scope:
            # A requested database the role cannot even enumerate is missing
            # evidence, not covered scope — it must degrade the status.
            wanted = scope.all_databases()
            visible = {d.upper() for d in databases}
            for missing in sorted(wanted - visible):
                failures.append(
                    f"{missing}: not visible to this role (missing or unauthorized)"
                )
                failure_kinds.add("unavailable")
            databases = [d for d in databases if d.upper() in wanted]
        for database in databases:
            try:
                sql = _render(
                    load_sql("information_schema", ex.info_schema_sql),
                    scope_pred=scope_pred,
                    window_days=ex.window_days,
                    database=database,
                )
                ing.ingest(source.reader(sql))
                succeeded.append(database)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{database}: {exc}")
                failure_kinds.add(_classify(exc))
        run.rows_written = ing.finish()
        run.source_used = "information_schema"
        run.actual_scope = succeeded
        # Only privilege-shaped errors may read as "unavailable"; anything else
        # is a real failure and must not masquerade as a permissions gap.
        privilege_only = failure_kinds == {"unavailable"}
        if failures and succeeded:
            run.status = "partial"
            run.error_category = "privilege" if privilege_only else "error"
            run.error_detail = "some databases could not be read: " + "; ".join(failures[:5])
            run.retryable = True
        elif failures:
            run.status = "unavailable" if privilege_only else "failed"
            run.error_category = "privilege" if privilege_only else "error"
            run.error_detail = "no database could be read: " + "; ".join(failures[:5])
            run.retryable = not privilege_only
        else:
            run.status = "complete"
            if au_error is not None:
                run.error_detail = (
                    f"ACCOUNT_USAGE not accessible ({au_error}); "
                    "used INFORMATION_SCHEMA fallback"
                )
        return run

    # No fallback exists.
    run.status = "unavailable"
    run.error_category = "privilege"
    run.error_detail = (
        f"ACCOUNT_USAGE not accessible and no INFORMATION_SCHEMA fallback exists"
        + (f": {au_error}" if au_error else "")
    )
    run.retryable = False
    return run
