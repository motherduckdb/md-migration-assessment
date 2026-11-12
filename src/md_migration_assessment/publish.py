"""``md-assess publish``: upload the handoff to MotherDuck and create the Dive.

Publishing is a separate, explicit step. It never uploads the private
collection: it builds the reduced handoff (no source bodies, no query text;
see :mod:`.handoff`) and uploads *that* with ``CREATE DATABASE ... FROM
'<file>'``, then creates a MotherDuck Dive over it from the bundled dashboard
source, so the same dashboard ``md-assess dashboard`` serves locally is
shareable inside the user's MotherDuck organization.

The Dive functions (``MD_LIST_DIVES``, ``MD_CREATE_DIVE``,
``MD_UPDATE_DIVE_CONTENT``, ``MD_UPDATE_DIVE_METADATA``) are called over an
ordinary DuckDB connection to ``md:``, the way motherduckdb/dive-sandbox's
deploy script does it. Re-running against the same title updates the Dive in
place; the database upload is refused if the name exists unless ``replace``.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

import duckdb

from .handoff import build_handoff
from .sources import get_adapter

ALIAS = "assessment"
PLACEHOLDER_PATH = "md:md_assessment"
DIVE_SOURCE_PATH = Path(__file__).resolve().parent / "dashboard" / "dive.tsx"
DIVE_URL = "https://app.motherduck.com/dives/{id}"
TITLE_TEMPLATE = "{source} → MotherDuck migration assessment"
TOKEN_ENV = ("MOTHERDUCK_TOKEN", "motherduck_token")
BUNDLE_HINT = "the Dive source is not bundled; run `npm install && npm run bundle` in dive/"


class Connection(Protocol):
    """The slice of a DuckDB connection publish uses (tests substitute a fake)."""

    def execute(self, sql: str) -> Any: ...
    def close(self) -> None: ...


@dataclass
class PublishResult:
    database: str
    handoff_path: Path | None
    handoff_manifest: dict
    dive_id: str
    dive_url: str
    dive_created: bool
    title: str
    statements: list[str] = field(default_factory=list)


def sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def sql_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def database_name_for(deployment: str | None) -> str:
    """Default MotherDuck database name: ``md_assessment_<deployment>``, lower-cased
    and reduced to ``[a-z0-9_]`` so it needs no quoting anywhere."""
    slug = re.sub(r"[^a-z0-9]+", "_", (deployment or "").lower()).strip("_")
    name = "md_assessment" + (f"_{slug}" if slug else "")
    return name[:60].rstrip("_")


def dive_source(database: str, path: Path = DIVE_SOURCE_PATH) -> str:
    """The bundled Dive with REQUIRED_DATABASES pointed at ``md:<database>``."""
    if not path.is_file():
        raise FileNotFoundError(BUNDLE_HINT)
    source = path.read_text(encoding="utf-8")
    if PLACEHOLDER_PATH not in source:
        raise ValueError(f"bundled Dive at {path} has no {PLACEHOLDER_PATH!r} placeholder to rewrite")
    return source.replace(PLACEHOLDER_PATH, f"md:{database}")


def resolve_token(token: str | None = None) -> str:
    for candidate in (token, *(os.environ.get(k) for k in TOKEN_ENV)):
        if candidate:
            return candidate
    raise ValueError(
        "no MotherDuck token: set MOTHERDUCK_TOKEN in the environment "
        "(Settings → Access tokens in the MotherDuck UI)"
    )


def connect_motherduck(token: str | None = None) -> duckdb.DuckDBPyConnection:
    """A DuckDB session connected to MotherDuck; the token never touches disk."""
    return duckdb.connect("md:", config={"motherduck_token": resolve_token(token)})


def _collection_facts(db_path: Path) -> dict[str, Any]:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        row = con.execute(
            "SELECT source_deployment, source_region, tool_version, "
            "strftime(started_at, '%Y-%m-%d'), source_kind FROM meta.collections "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f"{db_path} has no collection in meta.collections")
    return {"deployment": row[0], "region": row[1], "tool_version": row[2], "collected": row[3], "source_kind": row[4]}


def _required_resources_sql(database: str) -> str:
    return f"[{{'url': {sql_literal('md:' + database)}, 'alias': {sql_literal(ALIAS)}}}]"


def _upload(con: Connection, database: str, handoff: Path, *, replace: bool, log: list[str]) -> None:
    create = f"CREATE DATABASE {sql_ident(database)} FROM {sql_literal(str(handoff))}"
    try:
        log.append(create)
        con.execute(create)
    except duckdb.Error as exc:
        if "already exists" not in str(exc).lower():
            raise
        if not replace:
            raise ValueError(
                f"MotherDuck database {database!r} already exists; re-run with --replace to "
                "replace its contents, or pick another --name"
            ) from exc
        _replace_in_place(con, database, handoff, log=log)


def _handoff_tables(handoff: Path) -> list[tuple[str, str]]:
    """The (schema, table) pairs in the handoff file. The handoff holds only tables."""
    con = duckdb.connect(str(handoff), read_only=True)
    try:
        return [(s, t) for s, t in con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() ORDER BY 1, 2").fetchall()]
    finally:
        con.close()


def _replace_in_place(con: Connection, database: str, handoff: Path, *, log: list[str]) -> None:
    """Replace the database's tables with the handoff's, without dropping the database.

    Dropping it would fail (or, with CASCADE, delete them) when the database
    has shares, and a Flight that refreshes a shared database must keep them.
    So the handoff is attached read-only, every table is replaced in one
    transaction, and tables the new handoff no longer has are dropped, along
    with any schema that this leaves empty.
    """
    db = sql_ident(database)
    src = "md_assess_handoff"
    tables = _handoff_tables(handoff)
    wanted = {(s.lower(), t.lower()) for s, t in tables}
    wanted_schemas = {s.lower() for s, _ in tables}
    attach = f"ATTACH {sql_literal(str(handoff))} AS {src} (READ_ONLY)"
    log.append(attach)
    con.execute(attach)
    try:
        # Catalog names match case-insensitively, so the lookup must too, or a
        # --name in another case would leave stale tables behind.
        in_db = f"lower(database_name) = lower({sql_literal(database)})"
        existing_sql = f"SELECT schema_name, table_name FROM duckdb_tables() WHERE {in_db}"
        log.append(existing_sql)
        existing = con.execute(existing_sql).fetchall() or []
        # Schemas holding anything besides tables (views, macros, sequences,
        # types) are kept: DROP SCHEMA would fail on them and sink the replace.
        views_sql = (
            f"SELECT schema_name FROM duckdb_views() WHERE {in_db} AND NOT internal "
            f"UNION SELECT schema_name FROM duckdb_functions() WHERE {in_db} AND NOT internal "
            f"UNION SELECT schema_name FROM duckdb_sequences() WHERE {in_db} "
            f"UNION SELECT schema_name FROM duckdb_types() WHERE {in_db} AND NOT internal"
        )
        log.append(views_sql)
        schemas_with_views = {r[0].lower() for r in (con.execute(views_sql).fetchall() or [])}
        stale = [(s, t) for s, t in existing if (s.lower(), t.lower()) not in wanted]
        # A schema the handoff no longer has is dropped once its tables are,
        # unless something else still lives in it.
        empty_schemas = sorted({s for s, _ in stale
                                if s.lower() not in wanted_schemas
                                and s.lower() not in schemas_with_views
                                and s.lower() != "main"})
        steps = ["BEGIN TRANSACTION"]
        steps += [f"CREATE SCHEMA IF NOT EXISTS {db}.{sql_ident(s)}" for s in sorted({s for s, _ in tables})]
        steps += [f"CREATE OR REPLACE TABLE {db}.{sql_ident(s)}.{sql_ident(t)} AS "
                  f"FROM {src}.{sql_ident(s)}.{sql_ident(t)}" for s, t in tables]
        steps += [f"DROP TABLE {db}.{sql_ident(s)}.{sql_ident(t)}" for s, t in stale]
        steps += [f"DROP SCHEMA {db}.{sql_ident(s)}" for s in empty_schemas]
        steps.append("COMMIT")
        for step in steps:
            log.append(step)
            try:
                con.execute(step)
            except Exception:
                if step != "BEGIN TRANSACTION":
                    try:
                        con.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001 - keep the error that caused it
                        pass
                raise
    finally:
        log.append(f"DETACH {src}")
        con.execute(f"DETACH {src}")


def _upsert_dive(
    con: Connection, *, title: str, description: str, content: str, database: str, log: list[str]
) -> tuple[str, bool]:
    find = f"SELECT id FROM MD_LIST_DIVES() WHERE title = {sql_literal(title)}"
    log.append(find)
    rows = con.execute(find).fetchall()
    if len(rows) > 1:
        raise ValueError(f"{len(rows)} Dives are titled {title!r}; pass a unique --title")
    resources = _required_resources_sql(database)
    if rows:
        dive_id = str(rows[0][0])
        update = (
            f"FROM MD_UPDATE_DIVE_CONTENT(id = {sql_literal(dive_id)}::UUID, content = {sql_literal(content)}, "
            f"api_version = 1, required_resources = {resources})"
        )
        meta = (
            f"FROM MD_UPDATE_DIVE_METADATA(id = {sql_literal(dive_id)}::UUID, title = {sql_literal(title)}, "
            f"description = {sql_literal(description)})"
        )
        log.extend([update, meta])
        con.execute(update)
        con.execute(meta)
        return dive_id, False
    create = (
        f"SELECT id FROM MD_CREATE_DIVE(title = {sql_literal(title)}, content = {sql_literal(content)}, "
        f"description = {sql_literal(description)}, api_version = 1, required_resources = {resources})"
    )
    log.append(create)
    row = con.execute(create).fetchone()
    if not row:
        raise RuntimeError("MD_CREATE_DIVE returned no id")
    return str(row[0]), True


def publish(
    db_path: Path,
    *,
    database: str | None = None,
    title: str | None = None,
    replace: bool = False,
    handoff_dir: Path | None = None,
    keep_handoff: bool = False,
    connect: Callable[[], Connection] = connect_motherduck,
    dive_source_path: Path = DIVE_SOURCE_PATH,
) -> PublishResult:
    """Build the handoff, upload it, and create or update the Dive."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(f"assessment database not found: {db_path}")
    facts = _collection_facts(db_path)
    database = database or database_name_for(facts["deployment"])
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
        raise ValueError(f"database name {database!r} must be an identifier ([A-Za-z_][A-Za-z0-9_]*)")
    where = " · ".join(x for x in (facts["deployment"], facts["region"]) if x)
    adapter = get_adapter(facts["source_kind"] or "snowflake")
    default_title = TITLE_TEMPLATE.format(source=getattr(adapter, "display_name", adapter.name.title()))
    title = title or (f"{default_title} · {facts['deployment']}" if facts["deployment"] else default_title)
    description = (
        f"Read-only dashboard over the md-assess {facts['tool_version']} handoff of {getattr(adapter, 'deployment_noun', adapter.name.title() + ' deployment')} "
        f"{where or '(not recorded)'}, collected {facts['collected']}. Facts and coverage only: "
        "no source bodies or query text were uploaded."
    )
    # Read the bundled source before touching the network so a missing bundle fails fast.
    content = dive_source(database, dive_source_path)

    tmp: tempfile.TemporaryDirectory | None = None
    if handoff_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="md-assess-publish-")
        handoff_dir = Path(tmp.name)
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff = handoff_dir / f"{database}.handoff.duckdb"
    try:
        manifest = build_handoff(str(db_path), str(handoff))
        log: list[str] = []
        con = connect()
        try:
            _upload(con, database, handoff, replace=replace, log=log)
            dive_id, created = _upsert_dive(
                con, title=title, description=description, content=content, database=database, log=log
            )
        finally:
            con.close()
        return PublishResult(
            database=database,
            handoff_path=handoff if (keep_handoff or tmp is None) else None,
            handoff_manifest=manifest,
            dive_id=dive_id,
            dive_url=DIVE_URL.format(id=dive_id),
            dive_created=created,
            title=title,
            statements=log,
        )
    finally:
        if tmp is not None and not keep_handoff:
            tmp.cleanup()
