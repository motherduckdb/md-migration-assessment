"""A minimal, entirely synthetic source adapter.

This is the proof that the adapter seam is open: it exercises every hook
the neutral core calls (connection, strategies, error classification,
scope grammar, signals, fact builders, raw schema version) without any
warehouse client and without any Snowflake vocabulary. The neutral tests
in ``test_adapter_seam.py`` run the runner, resume, report, and handoff
against it.

Toy estate: a warehouse whose catalog exposes a deployment-wide view
(``sys.all_things``, gated behind ``standard``) and a per-database catalog
(``<db>.catalog.things``); a ``standard``-only global view of widget
sizes; and a ``LIST GIZMOS`` command with server-defined columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import duckdb
import pyarrow as pa

from md_migration_assessment.collect.extractor import (
    Command,
    Extractor,
    GlobalQuery,
    PerDatabaseQuery,
    Profile,
)
from md_migration_assessment.privacy import PrivacyClass
from md_migration_assessment.report.facts import table_exists
from md_migration_assessment.report.signals import PlannedSignal, probe
from md_migration_assessment.sources.base import ScopeGrammar, SessionInfo

SERVER_ANCHOR = datetime(2030, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

DENIED = "permission denied for relation"

# ── extract SQL "resources" (inline; a real adapter ships files) ──────────
SQL = {
    "global/things.sql": (
        "-- fake-extract: things/global\n"
        "SELECT db_name, schema_name, thing_name, thing_kind, owner, body\n"
        "FROM sys.all_things WHERE true {scope_filter}"
    ),
    "per_db/things.sql": (
        "-- fake-extract: things/per_db\n"
        "SELECT db_name, schema_name, thing_name, thing_kind, owner, body\n"
        "FROM {database}.catalog.things WHERE true {scope_filter}"
    ),
    "global/widget_sizes.sql": (
        "-- fake-extract: widget_sizes/global\n"
        "SELECT db_name, widget_name, bytes\n"
        "FROM sys.widget_sizes WHERE true {scope_filter}"
    ),
    "global/events.sql": (
        "-- fake-extract: events/global\n"
        "SELECT event_day, n_events\n"
        "FROM sys.events WHERE event_day >= today() - {window_days} {scope_filter}"
    ),
}

EXTRACTORS: list[Extractor] = [
    Extractor(
        name="things",
        category="catalog",
        min_profile=Profile.LITE,
        sources=(
            GlobalQuery("global/things.sql", "global", min_profile=Profile.STANDARD),
            PerDatabaseQuery("per_db/things.sql", "per_db"),
        ),
        scope_columns={"database": "db_name", "schema": "schema_name"},
        required_privilege="catalog reader",
        sensitive_fields={
            "db_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "thing_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "body": PrivacyClass.SOURCE_BODY,
        },
    ),
    Extractor(
        name="widget_sizes",
        category="sizing",
        min_profile=Profile.STANDARD,
        sources=(GlobalQuery("global/widget_sizes.sql", "global", min_profile=Profile.STANDARD),),
        scope_columns={"database": "db_name"},
        required_privilege="sizing reader",
        sensitive_fields={
            "db_name": PrivacyClass.OBJECT_NAME,
            "widget_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="gizmos",
        category="features",
        min_profile=Profile.LITE,
        sources=(Command(
            "LIST GIZMOS", ("name", "db_name", "schema_name", "flavor"), "list",
            visibility_bound=True,
        ),),
        scope_columns={"database": "db_name", "schema": "schema_name"},
        required_privilege="any role",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "db_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="events",
        category="workload",
        min_profile=Profile.STANDARD,
        sources=(GlobalQuery("global/events.sql", "global", min_profile=Profile.STANDARD),),
        window_days=30,
        window_from_history_days=True,
        window_end_lag_minutes=10,
        required_privilege="events reader",
    ),
]

SIGNALS = [
    probe("spicy_things", "table_layout", "things",
          "thing_kind = 'SPICY'", "db_name || '.' || schema_name || '.' || thing_name",
          exclude="db_name <> 'system_db'"),
    probe("mint_gizmos", "platform", "gizmos", "flavor = 'mint'", "name"),
]
PLANNED_SIGNALS = [PlannedSignal("unicorns", "platform", "no unicorn catalog exists yet")]


def _build_widget_totals(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS report")
    con.execute("DROP TABLE IF EXISTS report.widget_totals")
    con.execute(
        "CREATE TABLE report.widget_totals (collection_id UUID, db_name VARCHAR, "
        "total_bytes BIGINT, sizes_extract_status VARCHAR)"
    )
    if not table_exists(con, "raw", "widget_sizes"):
        return
    con.execute("""
        INSERT INTO report.widget_totals BY NAME
        SELECT w.collection_id AS collection_id, w.db_name AS db_name,
               sum(w.bytes)::BIGINT AS total_bytes,
               any_value(r.status) AS sizes_extract_status
        FROM raw.widget_sizes w
        LEFT JOIN meta.extract_runs r
          ON r.collection_id = w.collection_id AND r.extractor = 'widget_sizes'
        GROUP BY ALL
    """)


# The fake warehouse lower-cases identifiers and quotes with backticks —
# deliberately unlike Snowflake, so the grammar hook is really exercised.
GRAMMAR = ScopeGrammar(
    identifier_re=re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$"),
    normalize=str.lower,
    quote=lambda n: "`" + n.replace("`", "``") + "`",
    levels=("project", "dataset"),
)


@dataclass(frozen=True)
class FakeAdapter:
    name: str = "fakewh"
    raw_schema_version: int = 1
    extractors: list = field(default_factory=lambda: EXTRACTORS)
    scope: ScopeGrammar = GRAMMAR
    signals: list = field(default_factory=lambda: SIGNALS)
    planned_signals: list = field(default_factory=lambda: PLANNED_SIGNALS)
    fact_builders: tuple = (_build_widget_totals,)

    def open(self):
        raise RuntimeError("the fake adapter has no environment to connect from")

    def load_sql(self, path: str) -> str:
        return SQL[path]

    def classify_error(self, exc: BaseException) -> str:
        return "unavailable" if DENIED in str(exc) else "failed"


ADAPTER = FakeAdapter()

_MARKER_RE = re.compile(r"--\s*fake-extract:\s*(\w+)/(\w+)")
_DB_RE = re.compile(r"FROM `((?:[^`]|``)+)`\.catalog")


def things_table(rows) -> pa.Table:
    cols = {"db_name": [], "schema_name": [], "thing_name": [], "thing_kind": [], "owner": [], "body": []}
    for r in rows:
        for k in cols:
            cols[k].append(r.get(k))
    return pa.table(cols)


DEFAULT_THINGS = things_table([
    dict(db_name="appdb", schema_name="s1", thing_name="plain", thing_kind="PLAIN",
         owner="alice", body="CREATE THING plain"),
    dict(db_name="appdb", schema_name="s1", thing_name="hot", thing_kind="SPICY",
         owner="alice", body="CREATE THING hot"),
    dict(db_name="system_db", schema_name="sys", thing_name="furniture", thing_kind="SPICY",
         owner="system", body=None),
])
DEFAULT_WIDGETS = pa.table({"db_name": ["appdb", "appdb"], "widget_name": ["w1", "w2"], "bytes": [100, 250]})
DEFAULT_GIZMOS = pa.table({
    "name": ["g_mint", "g_plain", "g_global"],
    "db_name": ["appdb", "appdb", None],
    "schema_name": ["s1", "s2", None],
    "flavor": ["mint", "plain", "mint"],
})
DEFAULT_EVENTS = pa.table({"event_day": ["2029-12-31"], "n_events": [7]})


class FakeConnection:
    """Behavior keyed by (extractor, strategy label); errors are raised.

    ``global_data[name]`` / ``per_db_data[name][database]`` /
    ``command_data[name]`` hold a pa.Table or an exception to raise.
    """

    def __init__(
        self,
        global_data: dict | None = None,
        per_db_data: dict | None = None,
        command_data: dict | None = None,
        databases: list[str] | None = None,
        deployment: str = "fake-proj-1",
    ) -> None:
        self.global_data = {
            "things": DEFAULT_THINGS, "widget_sizes": DEFAULT_WIDGETS, "events": DEFAULT_EVENTS,
        }
        self.global_data.update(global_data or {})
        self.per_db_data = per_db_data or {}
        self.command_data = {"gizmos": DEFAULT_GIZMOS}
        self.command_data.update(command_data or {})
        self.databases = databases if databases is not None else ["appdb", "otherdb"]
        #: an exception here is raised from list_databases()
        self.enumeration_error: BaseException | None = None
        self.deployment = deployment
        self.queries: list[str] = []
        self.closed = False

    def reader(self, sql: str):
        self.queries.append(sql)
        m = _MARKER_RE.search(sql)
        assert m, f"FakeConnection got unrecognized SQL: {sql}"
        name, label = m.group(1), m.group(2)
        if label == "global":
            result = self.global_data.get(name)
        else:
            dbm = _DB_RE.search(sql)
            assert dbm, sql
            database = dbm.group(1).replace("``", "`")
            per_db = self.per_db_data.get(name, {})
            result = per_db.get(database)
            if result is None:
                base = self.global_data.get(name)
                if isinstance(base, pa.Table) and "db_name" in base.column_names:
                    import pyarrow.compute as pc

                    result = base.filter(pc.equal(base["db_name"], database))
                else:
                    result = pa.table({"db_name": pa.array([], pa.string())})
        if isinstance(result, BaseException):
            raise result
        return result

    def command(self, command: str):
        self.queries.append(command)
        by_cmd = {e.command.command: e.name for e in EXTRACTORS if e.command}
        entry = self.command_data.get(by_cmd[command])
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, tuple):
            return entry
        return entry, False

    def list_databases(self) -> list[str]:
        if self.enumeration_error is not None:
            raise self.enumeration_error
        return list(self.databases)

    def session_info(self) -> SessionInfo:
        return SessionInfo(deployment=self.deployment, version="0.1-fake", region="moon-1")

    def server_time(self) -> datetime:
        self.queries.append("<server_time>")
        return SERVER_ANCHOR

    def close(self) -> None:
        self.closed = True
