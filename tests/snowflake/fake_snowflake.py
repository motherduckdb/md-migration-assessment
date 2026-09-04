from __future__ import annotations

import re
from datetime import datetime, timezone

import pyarrow as pa

from md_migration_assessment.sources.base import SessionInfo
from md_migration_assessment.sources.snowflake.manifest import (
    show_sql,
)

#: The fake's server clock: fixed so tests can assert coverage-window
#: arithmetic exactly (bounds must derive from the SERVER anchor, never the
#: client clock — client/server skew breaks ordering arguments).
SERVER_ANCHOR = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)

_AU_RE = re.compile(r"snowflake\.account_usage\.(\w+)", re.IGNORECASE)
# Several M3c aggregate extracts read the same view (QUERY_HISTORY), so the
# view name no longer identifies the extract; those SQL files carry an
# explicit marker comment the fake dispatches on first.
_MARKER_RE = re.compile(r"--\s*md-assess-extract:\s*(\w+)")
_IS_RE = re.compile(r'FROM\s+"((?:[^"]|"")+)"\.information_schema\.(\w+)', re.IGNORECASE)

#: INFORMATION_SCHEMA view -> extractor/table name where they differ.
_VIEW_TO_EXTRACTOR = {}


def small_table(**overrides) -> pa.Table:
    base = {"a": [1, 2], "b": ["x", "y"]}
    base.update(overrides)
    return pa.table(base)


class FakeSource:
    """Stands in for SnowflakeSource (the Snowflake Connection). Behavior is
    keyed by extractor name:

    - account_usage[name] -> pa.Table to return, or Exception to raise
    - info_schema[name][database] -> pa.Table or Exception
    Every SQL string seen is recorded in .queries for assertions.
    """

    def __init__(
        self,
        account_usage: dict | None = None,
        info_schema: dict | None = None,
        databases: list[str] | None = None,
        show_data: dict | None = None,
        account: str = "TESTACCT",
    ) -> None:
        self.account_usage = account_usage or {}
        self.info_schema = info_schema or {}
        self.show_data = show_data or {}
        self.databases = databases if databases is not None else ["DB1", "DB2"]
        self.account = account
        self.queries: list[str] = []

    def reader(self, sql: str):
        self.queries.append(sql)
        m = _MARKER_RE.search(sql)
        if m:
            name = m.group(1).lower()
            result = self.account_usage.get(name, small_table())
            # BaseException, not Exception: interruption tests inject
            # KeyboardInterrupt, which must propagate like the real thing
            if isinstance(result, BaseException):
                raise result
            return result
        m = _AU_RE.search(sql)
        if m:
            name = m.group(1).lower()
            result = self.account_usage.get(name, small_table())
            if isinstance(result, BaseException):
                raise result
            return result
        m = _IS_RE.search(sql)
        if m:
            database, view = m.group(1), m.group(2).lower()
            name = _VIEW_TO_EXTRACTOR.get(view, view)
            per_db = self.info_schema.get(name, {})
            if database in per_db:
                result = per_db[database]
            else:
                # Like real Snowflake: the same objects are visible through
                # INFORMATION_SCHEMA that ACCOUNT_USAGE reports (when the
                # account_usage entry is a table, not an injected error).
                au = self.account_usage.get(name)
                result = au if isinstance(au, pa.Table) else small_table()
            if isinstance(result, Exception):
                raise result
            return result
        raise AssertionError(f"FakeSource got unrecognized SQL: {sql}")

    def command(self, command: str):
        self.queries.append(command)
        from md_migration_assessment.sources.snowflake.manifest import EXTRACTORS, show_sql

        by_cmd = {show_sql(e): e.name for e in EXTRACTORS if show_sql(e)}
        name = by_cmd.get(command, command)
        entry = self.show_data.get(name, small_table())
        if isinstance(entry, Exception):
            raise entry
        if isinstance(entry, tuple):
            return entry
        return entry, False

    def list_databases(self) -> list[str]:
        return list(self.databases)

    def session_info(self) -> SessionInfo:
        return SessionInfo(deployment=self.account, version="9.9.9", region="AWS_US_WEST_2")

    def server_time(self):
        # recorded in .queries so tests can assert the anchor is captured
        # BEFORE the extract SQL executes
        self.queries.append("<server_time>")
        return SERVER_ANCHOR

    def close(self) -> None:
        pass


NOT_AUTHORIZED = "SQL compilation error: Object does not exist or not authorized."
