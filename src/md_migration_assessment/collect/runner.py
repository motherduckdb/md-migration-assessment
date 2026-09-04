"""Collection orchestration (source-neutral).

Runs every extractor the adapter declares and the profile requests, lands
results in ``raw.*`` stamped with the collection_id, and records one
``meta.extract_runs`` row per extractor — success or not. A partial
collection is always a valid output; missing evidence is recorded, never
silently omitted (spec §2).

Status semantics:

- ``not_requested``: the chosen profile does not include this extractor.
- ``complete``: the extract covered its full requested scope.
- ``partial``: a per-database walk succeeded for some databases and failed
  for others (``actual_scope`` lists coverage), or a command's output was
  truncated by the server.
- ``unavailable``: no acquisition strategy was accessible to this
  role/edition (classified by the adapter), and none succeeded.
- ``failed``: unexpected error; ``retryable`` is true.
- ``interrupted``: the user stopped the collection (Ctrl+C) during or
  before this extractor. Every extractor still gets a row — an interrupted
  collection is a valid partial output, and ``resume=True`` re-runs exactly
  the extractors that are not ``complete``/``not_requested``.

Acquisition strategies (see :mod:`.extractor`) are tried in the order the
extractor declares them: a strategy the adapter classifies as unavailable
falls through to the next; a real failure stops the extractor as
``failed``; a strategy gated behind a higher profile is skipped silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable

import duckdb
import pyarrow as pa

from .. import db
from ..db import Collection, ExtractRun, utcnow
from ..sources.base import (
    DEFAULT_IDENTIFIER_RE,
    DEFAULT_SCOPE_GRAMMAR,
    Connection,
    ScopeGrammar,
    SourceAdapter,
)
from .extractor import (
    Command,
    Extractor,
    GlobalQuery,
    PerDatabaseQuery,
    Profile,
    extractor_version,
)


@dataclass(frozen=True)
class Scope:
    """Parsed --scope entries: database names and database.schema pairs."""

    databases: frozenset[str]
    schemas: frozenset[tuple[str, str]]

    @classmethod
    def parse(
        cls,
        entries: Iterable[str] | None,
        grammar: ScopeGrammar = DEFAULT_SCOPE_GRAMMAR,
    ) -> "Scope | None":
        if not entries:
            return None
        dbs: set[str] = set()
        schemas: set[tuple[str, str]] = set()
        db_level, schema_level = grammar.levels
        for entry in entries:
            parts = entry.split(".")
            if len(parts) not in (1, 2) or not all(
                grammar.identifier_re.match(p) for p in parts
            ):
                raise ValueError(
                    f"invalid --scope entry {entry!r}: use {db_level.upper()} or "
                    f"{db_level.upper()}.{schema_level.upper()} "
                    "(quoted/special-character identifiers are not supported yet)"
                )
            parts = [grammar.normalize(p) for p in parts]
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


def _render(
    sql: str,
    *,
    scope_pred: str,
    window_days: int | None,
    database: str | None = None,
    quote: Callable[[str], str] | None = None,
) -> str:
    scope_filter = f"AND ({scope_pred})" if scope_pred else ""
    out = sql.replace("{scope_filter}", scope_filter)
    if window_days is not None:
        out = out.replace("{window_days}", str(int(window_days)))
    if database is not None:
        # Discovered database names come from the source itself and may
        # contain spaces, punctuation, or mixed case — quote, never reject.
        assert quote is not None
        literal = database.replace("'", "''")
        out = out.replace("{database}", quote(database)).replace(
            "{database_literal}", literal
        )
    return out


#: Columns stamped onto every raw row by the framework rather than produced
#: by extract SQL. The handoff builder exempts exactly this set from its
#: expected-column policy — keep the two in lockstep by sharing the constant.
FRAMEWORK_COLUMNS = frozenset({"collection_id"})


class _Ingestor:
    """Lands Arrow data in raw.<target>, stamping collection_id on every row."""

    def __init__(self, con: duckdb.DuckDBPyConnection, coll: Collection, target: str):
        if not DEFAULT_IDENTIFIER_RE.match(target):
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


#: Statuses --resume keeps as-is; everything else is re-run (a re-run's
#: CREATE OR REPLACE ingest cleanly replaces any partial rows).
_RESUME_KEEP = frozenset({"complete", "not_requested"})


def _emit(progress: "Callable[[str], None] | None", msg: str) -> None:
    if progress is not None:
        progress(msg)


def run_collection(
    con: duckdb.DuckDBPyConnection,
    adapter: SourceAdapter,
    source: Connection,
    *,
    profile: Profile,
    scope: Scope | None = None,
    history_days: int = 30,
    query_text_mode: str = "never_collected",
    mode: str = "local",
    progress: "Callable[[str], None] | None" = None,
    resume: bool = False,
) -> Collection:
    """Run (or resume) a collection.

    On ``resume=True`` the stored collection is authoritative: profile,
    scope, --history-days, source kind, and privacy settings come from
    meta.collections and the passed values are ignored — a resume continues
    the original collection, it never silently redefines it. Extractors
    already ``complete`` or ``not_requested`` are skipped; everything else
    (``failed``, ``partial``, ``unavailable``, ``interrupted``, missing) is
    re-run and its coverage row replaced.

    A KeyboardInterrupt (Ctrl+C) is caught only long enough to keep the
    coverage contract: the in-flight extractor and every unattempted one get
    an ``interrupted`` row, ``finished_at`` stays NULL, and the interrupt is
    re-raised. The database is a valid partial collection at every point.
    """
    info = source.session_info()
    extractors = adapter.extractors

    if resume:
        coll = db.load_collection(con)
        if coll.source_kind != adapter.name:
            raise ValueError(
                f"cannot resume: this database was collected from source "
                f"{coll.source_kind!r} but the adapter in use is {adapter.name!r}"
            )
        if coll.raw_schema_version != adapter.raw_schema_version:
            raise ValueError(
                f"cannot resume: collection has raw schema "
                f"v{coll.raw_schema_version}, this tool writes "
                f"v{adapter.raw_schema_version}. Re-collect into a new file."
            )
        # Mixing deployments inside one collection would silently blend two
        # estates' evidence under one collection_id.
        if coll.source_deployment and info.deployment and (
            coll.source_deployment != info.deployment
        ):
            raise ValueError(
                f"cannot resume: this database was collected from "
                f"{coll.source_deployment!r} but the connection is to "
                f"{info.deployment!r}"
            )
        profile = Profile.parse(coll.profile)
        scope = Scope.parse(coll.scope, adapter.scope)
        history_days = coll.history_days
        # A finished collection is still resumable (retrying failed or
        # unavailable extractors after grants are fixed); reopen it BEFORE
        # re-running anything so an interrupted retry can never leave a
        # 'finished' collection containing interrupted extractors
        # (review, 2026-08-19). finish_collection re-stamps it at the end.
        db.reopen_collection(con, coll)
        prior = {
            r[0]: r[1]
            for r in con.execute(
                "SELECT extractor, status FROM meta.extract_runs "
                "WHERE collection_id = ?",
                [str(coll.collection_id)],
            ).fetchall()
        }
        _emit(progress, (
            f"resuming collection {coll.collection_id} "
            f"(profile={coll.profile}, history_days={history_days}, "
            f"{sum(1 for s in prior.values() if s in _RESUME_KEEP)} of "
            f"{len(extractors)} extractors already done)"
        ))
    else:
        # Raw evidence is immutable: ingest uses CREATE OR REPLACE per
        # extract, so a second collection in the same file would silently
        # destroy the first collection's raw.* rows while meta.collections
        # still listed both.
        existing = con.execute("SELECT count(*) FROM meta.collections").fetchone()[0]
        if existing:
            raise ValueError(
                "output database already contains a collection; write each "
                "collection to a new file, or pass --resume to continue an "
                "incomplete one"
            )
        coll = Collection(
            profile=profile.name.lower(),
            source_kind=adapter.name,
            raw_schema_version=adapter.raw_schema_version,
            mode=mode,
            scope=scope.as_list() if scope else None,
            query_text_mode=query_text_mode,
            history_days=history_days,
            source_deployment=info.deployment,
            source_version=info.version,
            source_region=info.region,
            source_edition=info.edition,
        )
        db.begin_collection(con, coll)
        prior = {}

    # The interrupt guard wraps the WHOLE orchestration, not just extractor
    # execution: a Ctrl+C during a progress write, a coverage-row insert, or
    # finish_collection must still leave every extractor with a coverage row
    # (review, 2026-08-19). The handler repairs after the fact rather than
    # tracking position: any extractor missing a row gets 'interrupted'.
    try:
        total = len(extractors)
        for i, ex in enumerate(extractors):
            prior_status = prior.get(ex.name)
            if prior_status in _RESUME_KEEP:
                _emit(progress, f"[{i + 1}/{total}] {ex.name}: skipped "
                                f"({prior_status} in existing collection)")
                continue
            if prior_status is not None:
                db.delete_extract_run(con, coll, ex.name)
            # Replace, never accrete: before ANY run, the extractor's raw
            # target must not exist — unconditionally, because stale raw
            # evidence can exist even WITHOUT a coverage row (a kill between
            # ingest and coverage recording), and a retry that fails before
            # ingesting must not leave a previous attempt's rows behind a
            # failed status (review rounds 1-2, 2026-08-19).
            if not DEFAULT_IDENTIFIER_RE.match(ex.target_table):
                raise ValueError(f"bad target table name {ex.target_table!r}")
            con.execute(f'DROP TABLE IF EXISTS raw."{ex.target_table}"')
            _emit(progress, f"[{i + 1}/{total}] {ex.name}: running")
            run = _run_extractor(
                con, adapter, source, coll, ex, profile, scope, history_days
            )
            db.record_extract_run(con, coll, run)
            secs = (utcnow() - run.started_at).total_seconds()
            line = (
                f"[{i + 1}/{total}] {ex.name}: {run.status} "
                f"({run.rows_written or 0} rows, {secs:.1f}s)"
            )
            if run.status not in ("complete", "not_requested") and run.error_detail:
                line += f" — {run.error_detail[:120]}"
            _emit(progress, line)

        db.finish_collection(con, coll)
    except KeyboardInterrupt:
        _ensure_interrupt_coverage(con, adapter, coll, scope, history_days, progress)
        raise
    return coll


def _ensure_interrupt_coverage(
    con: duckdb.DuckDBPyConnection,
    adapter: SourceAdapter,
    coll: Collection,
    scope: Scope | None,
    history_days: int,
    progress: "Callable[[str], None] | None",
) -> None:
    """Ctrl+C landed somewhere in the orchestration: repair the coverage
    contract by recording an 'interrupted' row for every extractor that has
    none, and make sure the collection reads as unfinished — the interrupt
    can even land after finish_collection commits but before it returns
    (review round 2, 2026-08-19), so finished_at is explicitly cleared: a
    run that exits by interrupt never leaves a 'finished' stamp. If the
    work truly all completed, the next --resume skips everything and
    re-stamps it."""
    db.reopen_collection(con, coll)
    have = {
        r[0]
        for r in con.execute(
            "SELECT extractor FROM meta.extract_runs WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    }
    for ex in adapter.extractors:
        if ex.name in have:
            continue
        run = _base_run(adapter, ex, scope, _effective_window(ex, history_days))
        run.status = "interrupted"
        run.error_category = "error"
        run.retryable = True
        run.error_detail = (
            "collection interrupted before this extractor completed; any "
            "partial raw rows are replaced on --resume"
        )
        db.record_extract_run(con, coll, run)
    _emit(progress, "interrupted — state saved; continue with --resume")


def _effective_window(ex: Extractor, history_days: int) -> int | None:
    """Workload time-series extracts take their window from --history-days;
    everything else keeps its fixed manifest default."""
    return history_days if ex.window_from_history_days else ex.window_days


def _base_run(
    adapter: SourceAdapter, ex: Extractor, scope: Scope | None, window_days: int | None
) -> ExtractRun:
    return ExtractRun(
        extractor=ex.name,
        extractor_version=extractor_version(ex, adapter.load_sql),
        target_table=ex.target_table,
        status="failed",  # overwritten below
        started_at=utcnow(),
        requested_scope=scope.as_list() if scope else None,
        requested_window_days=window_days,
        required_privilege=ex.required_privilege or None,
        min_edition=ex.min_edition or None,
    )


def _run_extractor(
    con: duckdb.DuckDBPyConnection,
    adapter: SourceAdapter,
    source: Connection,
    coll: Collection,
    ex: Extractor,
    profile: Profile,
    scope: Scope | None,
    history_days: int,
) -> ExtractRun:
    window_days = _effective_window(ex, history_days)
    run = _base_run(adapter, ex, scope, window_days)

    if profile < ex.min_profile:
        run.status = "not_requested"
        run.error_detail = f"requires profile '{ex.min_profile.name.lower()}' or higher"
        return run

    scope_pred = scope.predicate(ex.scope_columns) if scope else ""

    # Strategies in declared order. 'unavailable' falls through to the next
    # one and is remembered so a successful fallback can disclose it; a real
    # failure stops here.
    unavailable: list[tuple[str, BaseException]] = []
    skipped_by_profile = False
    for strategy in ex.sources:
        if isinstance(strategy, Command):
            _run_command(source, con, coll, ex, strategy, scope, run, adapter)
            return run
        if isinstance(strategy, GlobalQuery):
            if profile < strategy.min_profile:
                skipped_by_profile = True
                continue
            try:
                sql = _render(
                    adapter.load_sql(strategy.sql),
                    scope_pred=scope_pred,
                    window_days=window_days,
                )
                # Anchor from the SERVER's clock, captured before the extract
                # executes: statement order on the one server clock guarantees
                # anchor <= the extract SQL's CURRENT_TIMESTAMP, so the
                # recorded window is a floor of true coverage regardless of
                # client clock skew or transfer time. Extracts with a latency
                # watermark observe up to now - lag; the gap is disclosed,
                # never observed.
                anchor = source.server_time() if window_days is not None else None
                ing = _Ingestor(con, coll, ex.target_table)
                ing.ingest(source.reader(sql))
                run.rows_written = ing.finish()
                run.status = "complete"
                run.source_used = strategy.label
                if window_days is not None:
                    run.actual_window_end = anchor - timedelta(
                        minutes=ex.window_end_lag_minutes
                    )
                    run.actual_window_start = anchor - timedelta(days=window_days)
                return run
            except Exception as exc:  # noqa: BLE001 — every failure becomes coverage metadata
                if adapter.classify_error(exc) == "failed":
                    run.status = "failed"
                    run.error_category = "error"
                    run.error_detail = f"{strategy.label} extract failed: {exc}"
                    run.retryable = True
                    return run
                unavailable.append((strategy.label, exc))
                continue
        if isinstance(strategy, PerDatabaseQuery):
            _run_per_database(
                source, con, coll, ex, strategy, scope, scope_pred, window_days,
                run, adapter, unavailable,
            )
            return run

    # Nothing succeeded and no strategy remains.
    run.status = "unavailable"
    run.error_category = "privilege"
    tried = "; ".join(f"{label}: {exc}" for label, exc in unavailable)
    if unavailable:
        run.error_detail = (
            f"no accessible source and no fallback exists ({tried})"
        )
    elif skipped_by_profile:
        run.error_detail = (
            f"requires a higher profile for its only source "
            f"(profile '{profile.name.lower()}')"
        )
    else:
        run.error_detail = "no source strategy applied"
    run.retryable = False
    return run


def _run_command(
    source: Connection,
    con: duckdb.DuckDBPyConnection,
    coll: Collection,
    ex: Extractor,
    strategy: Command,
    scope: Scope | None,
    run: ExtractRun,
    adapter: SourceAdapter,
) -> None:
    # Command output is deployment-wide; database-resident objects are
    # scope-filtered CLIENT-SIDE before anything is persisted — --scope is a
    # privacy boundary, and out-of-scope object names must not land in the
    # output database (found in review, 2026-08-18).
    try:
        table, truncated = source.command(strategy.command)
        scope_note = None
        achieved_scope: list[str] | None = None
        db_col = ex.scope_columns.get("database") if scope else None
        if db_col:
            schema_col = ex.scope_columns.get("schema")
            for required in filter(None, (db_col, schema_col)):
                if required not in table.column_names:
                    raise ValueError(
                        f"cannot scope-filter command output: expected column "
                        f"{required!r} missing from server response"
                    )
            db_vals = table.column(db_col).to_pylist()

            if schema_col:
                # Same semantics as the SQL scope predicate: plain-DB
                # entries admit the whole database, DB.SCHEMA entries
                # admit exactly that pair (a NULL schema there is
                # unattributable and drops). NULL database = deployment-level
                # row, attributable to no out-of-scope database; retained.
                schema_vals = table.column(schema_col).to_pylist()
                norm = adapter.scope.normalize

                def in_scope(db, schema) -> bool:
                    if db is None:
                        return True
                    d = norm(str(db))
                    if d in scope.databases:
                        return True
                    return (
                        schema is not None
                        and (d, norm(str(schema))) in scope.schemas
                    )

                keep = [
                    i for i, (d, s) in enumerate(zip(db_vals, schema_vals))
                    if in_scope(d, s)
                ]
                achieved_scope = scope.as_list()
            else:
                # Database-only inventory (e.g. shares): like the SQL
                # predicate, an extract with no schema granularity admits
                # the whole database for schema-scoped entries — dropping
                # them would falsely report observed_zero.
                wanted = scope.all_databases()
                norm = adapter.scope.normalize
                keep = [
                    i for i, d in enumerate(db_vals)
                    if d is None or norm(str(d)) in wanted
                ]
                achieved_scope = sorted(wanted)
            if len(keep) != table.num_rows:
                # typed indices: a bare [] infers a null-typed array and
                # Arrow's take has no kernel for it (found live)
                table = table.take(pa.array(keep, type=pa.int64()))
            scope_note = "command output filtered client-side to requested scope"
            if not schema_col and scope.schemas:
                scope_note += (
                    " (database granularity: this inventory has no schema column)"
                )
        elif scope:
            scope_note = (
                "collected deployment-wide: this inventory has no "
                "database residency to scope by"
            )
        ing = _Ingestor(con, coll, ex.target_table)
        ing.ingest(table)
        run.rows_written = ing.finish()
        run.source_used = strategy.label
        run.actual_scope = achieved_scope
        if truncated:
            run.status = "partial"
            run.error_category = "error"
            run.error_detail = (
                "command output truncated at the server-side row cap — "
                "inventory may be incomplete"
            )
            run.retryable = False
        else:
            run.status = "complete"
            run.error_detail = scope_note
    except Exception as exc:  # noqa: BLE001
        kind = adapter.classify_error(exc)
        run.status = kind if kind == "unavailable" else "failed"
        run.error_category = "privilege" if kind == "unavailable" else "error"
        run.error_detail = f"{strategy.label} extract failed: {exc}"
        run.retryable = kind != "unavailable"


def _run_per_database(
    source: Connection,
    con: duckdb.DuckDBPyConnection,
    coll: Collection,
    ex: Extractor,
    strategy: PerDatabaseQuery,
    scope: Scope | None,
    scope_pred: str,
    window_days: int | None,
    run: ExtractRun,
    adapter: SourceAdapter,
    unavailable_before: list[tuple[str, BaseException]],
) -> None:
    """One walk per accessible (or scoped) database."""
    try:
        databases = source.list_databases()
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error_category = "error"
        run.error_detail = f"could not enumerate databases: {exc}"
        run.retryable = True
        return
    norm = adapter.scope.normalize
    ing = _Ingestor(con, coll, ex.target_table)
    succeeded: list[str] = []
    failures: list[str] = []
    failure_kinds: set[str] = set()
    if scope:
        # A requested database the role cannot even enumerate is missing
        # evidence, not covered scope — it must degrade the status.
        wanted = scope.all_databases()
        visible = {norm(d) for d in databases}
        for missing in sorted(wanted - visible):
            failures.append(
                f"{missing}: not visible to this role (missing or unauthorized)"
            )
            failure_kinds.add("unavailable")
        databases = [d for d in databases if norm(d) in wanted]
    for database in databases:
        try:
            sql = _render(
                adapter.load_sql(strategy.sql),
                scope_pred=scope_pred,
                window_days=window_days,
                database=database,
                quote=adapter.scope.quote,
            )
            ing.ingest(source.reader(sql))
            succeeded.append(database)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{database}: {exc}")
            failure_kinds.add(adapter.classify_error(exc))
    run.rows_written = ing.finish()
    run.source_used = strategy.label
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
        if unavailable_before:
            prior = "; ".join(f"{label} not accessible ({exc})" for label, exc in unavailable_before)
            run.error_detail = f"{prior}; used {strategy.label} fallback"
