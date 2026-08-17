from __future__ import annotations

import re

import pyarrow as pa
import pytest

from md_migration_assessment.collect.snowflake import SessionInfo

_AU_RE = re.compile(r"snowflake\.account_usage\.(\w+)", re.IGNORECASE)
_IS_RE = re.compile(r"FROM\s+(\w+)\.information_schema\.(\w+)", re.IGNORECASE)

#: INFORMATION_SCHEMA view -> extractor/table name where they differ.
_VIEW_TO_EXTRACTOR = {}


def small_table(**overrides) -> pa.Table:
    base = {"a": [1, 2], "b": ["x", "y"]}
    base.update(overrides)
    return pa.table(base)


class FakeSource:
    """Stands in for SnowflakeSource. Behavior is keyed by extractor name:

    - account_usage[name] -> pa.Table to return, or Exception to raise
    - info_schema[name][database] -> pa.Table or Exception
    Every SQL string seen is recorded in .queries for assertions.
    """

    def __init__(
        self,
        account_usage: dict | None = None,
        info_schema: dict | None = None,
        databases: list[str] | None = None,
    ) -> None:
        self.account_usage = account_usage or {}
        self.info_schema = info_schema or {}
        self.databases = databases if databases is not None else ["DB1", "DB2"]
        self.queries: list[str] = []

    def reader(self, sql: str):
        self.queries.append(sql)
        m = _AU_RE.search(sql)
        if m:
            name = m.group(1).lower()
            result = self.account_usage.get(name, small_table())
            if isinstance(result, Exception):
                raise result
            return result
        m = _IS_RE.search(sql)
        if m:
            database, view = m.group(1), m.group(2).lower()
            name = _VIEW_TO_EXTRACTOR.get(view, view)
            per_db = self.info_schema.get(name, {})
            result = per_db.get(database, small_table())
            if isinstance(result, Exception):
                raise result
            return result
        raise AssertionError(f"FakeSource got unrecognized SQL: {sql}")

    def list_databases(self) -> list[str]:
        return list(self.databases)

    def session_info(self) -> SessionInfo:
        return SessionInfo(account="TESTACCT", version="9.9.9", region="AWS_US_WEST_2")


@pytest.fixture()
def out_db(tmp_path):
    from md_migration_assessment.db import open_output

    con = open_output(str(tmp_path / "assessment.duckdb"))
    yield con
    con.close()


NOT_AUTHORIZED = "SQL compilation error: Object does not exist or not authorized."
