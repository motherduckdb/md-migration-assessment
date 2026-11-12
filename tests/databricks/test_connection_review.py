"""Review fixes for the Databricks connection: scope before cap, LIKE escaping, one U2M sign-in, jobs cap and pagination."""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest

from md_migration_assessment.collect.runner import Scope
from md_migration_assessment.sources.databricks import ADAPTER
from md_migration_assessment.sources.databricks.connection import (
    DatabricksConfig,
    DatabricksSource,
    detail_targets_sql,
    job_task_rows,
)
from md_migration_assessment.sources.databricks.manifest import (
    JOB_TASKS_COMMAND,
    TABLE_DETAILS_COMMAND,
)


class _Cur:
    """Cursor over a fake connection that records SQL and answers table lists and DESCRIBE DETAIL."""

    def __init__(self, owner):
        self.o = owner
        self.rows: list[dict] = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.o.sql.append(sql)
        if sql.startswith("DESCRIBE DETAIL"):
            self.rows = [{"format": "delta"}]
        else:
            self.rows = self.o.targets(sql)

    def fetchall_arrow(self):
        if not self.rows:
            return pa.table({"table_catalog": pa.array([], pa.string()),
                             "table_schema": pa.array([], pa.string()),
                             "table_name": pa.array([], pa.string()),
                             "format": pa.array([], pa.string())})
        return pa.Table.from_pylist(self.rows)


class _Conn:
    """Fake SQL connection; ``targets(sql)`` decides what the table listing returns."""

    def __init__(self, targets):
        self.sql: list[str] = []
        self.targets = targets

    def cursor(self):
        return _Cur(self)

    def close(self):
        pass


def _estate():
    """DuckDB stand-in for information_schema.tables: 5 tables in an early catalog, 2 in the one we want."""
    db = duckdb.connect()
    db.execute("CREATE TABLE t (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR, "
               "table_type VARCHAR)")
    for i in range(5):
        db.execute(f"INSERT INTO t VALUES ('a_early', 's', 'x{i}', 'MANAGED')")
    db.execute("INSERT INTO t VALUES ('wanted', 's', 'one', 'MANAGED'), ('wanted', 's', 'two', 'MANAGED')")
    return db


# 1. scope before the cap ---------------------------------------------------


def test_scope_predicate_is_applied_before_the_cap():
    """The requested catalog gets its tables even when earlier catalogs are over the cap."""
    db = _estate()

    def targets(sql):
        sql = sql.replace("system.information_schema.tables", "t")
        return db.execute(sql).to_arrow_table().to_pylist()

    scope = Scope.parse(["wanted"], ADAPTER.scope)
    src = DatabricksSource(_Conn(targets), detail_max_tables=3)
    src.set_scope_predicate(scope.predicate({"database": "table_catalog", "schema": "table_schema"}))
    table, truncated = src.command(TABLE_DETAILS_COMMAND)
    assert table.column("table_name").to_pylist() == ["one", "two"]
    assert truncated is False

    unscoped = DatabricksSource(_Conn(targets), detail_max_tables=3)
    table, truncated = unscoped.command(TABLE_DETAILS_COMMAND)
    assert truncated is True and table.column("table_catalog").to_pylist() == ["a_early"] * 3


def test_detail_targets_sql_scope_predicate_sits_before_order_and_limit():
    """The scope predicate is a parenthesised AND clause ahead of ORDER BY/LIMIT."""
    sql = detail_targets_sql(5, "table_catalog IN ('c1') OR table_catalog = 'c2'")
    assert "AND (table_catalog IN ('c1') OR table_catalog = 'c2') ORDER BY" in sql
    assert "LIMIT 6" in sql and "AND (" not in detail_targets_sql(5).split("ESCAPE")[-1]


# 2. LIKE escaping ------------------------------------------------------------


def test_hidden_prefixes_are_escaped_and_do_not_match_lookalikes():
    """xymaterializationz_t is kept; real __materialization_ and event_log_ tables are dropped."""
    sql = detail_targets_sql(100)
    assert "'!_!_materialization!_%' ESCAPE '!'" in sql
    assert "'event!_log!_%' ESCAPE '!'" in sql
    # Databricks reads '\\' as an unterminated string, so no backslash escape
    assert "\\" not in sql
    db = duckdb.connect()
    db.execute("CREATE TABLE t (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR, "
               "table_type VARCHAR)")
    for name in ("xymaterializationz_t", "__materialization_mv", "event_log_abc", "eventxlogxabc",
                 "plain"):
        db.execute(f"INSERT INTO t VALUES ('c', 's', '{name}', 'MANAGED')")
    got = db.execute(sql.replace("system.information_schema.tables", "t")).fetchall()
    assert sorted(r[2] for r in got) == ["eventxlogxabc", "plain", "xymaterializationz_t"]


# 3. one U2M sign-in ----------------------------------------------------------


def _u2m_cfg():
    return DatabricksConfig(host="dbc-x.cloud.databricks.com", http_path="/sql/1.0/warehouses/w")


def test_u2m_builds_one_sdk_config_shared_by_sql_and_rest(monkeypatch):
    """Browser sign-in happens once: the SQL connector and the REST client share one SDK Config."""
    import databricks.sdk as sdk
    import databricks.sdk.core as sdk_core
    import databricks.sql as dbsql

    made, clients, sql_kwargs = [], [], {}

    class FakeConfig:
        def __init__(self, **kw):
            made.append(kw)

        def authenticate(self):
            return {"Authorization": "Bearer fake"}

    class FakeWs:
        def __init__(self, **kw):
            clients.append(kw)
            self.api_client = object()

    monkeypatch.setattr(sdk_core, "Config", FakeConfig)
    monkeypatch.setattr(sdk, "WorkspaceClient", FakeWs)
    monkeypatch.setattr(dbsql, "connect", lambda **kw: sql_kwargs.update(kw) or _Conn(lambda s: []))

    src = DatabricksSource.open(_u2m_cfg())
    src._rest_client()
    assert len(made) == 1 and made[0]["auth_type"] == "external-browser"
    assert "auth_type" not in sql_kwargs
    assert sql_kwargs["credentials_provider"]() == src._sdk_config.authenticate
    assert sql_kwargs["credentials_provider"]()()["Authorization"] == "Bearer fake"
    assert clients == [{"config": src._sdk_config}]


def test_pat_and_m2m_paths_are_unchanged(monkeypatch):
    """PAT passes the token; M2M keeps its own credentials provider; neither builds a U2M config."""
    monkeypatch.setattr(DatabricksConfig, "u2m_sdk_config",
                        lambda self: pytest.fail("U2M config built for a non-U2M login"))
    pat = DatabricksConfig(host="h", http_path="/p", token="dapi-x").connect_kwargs()
    assert pat["access_token"] == "dapi-x" and "credentials_provider" not in pat
    import databricks.sdk.core as sdk_core

    monkeypatch.setattr(sdk_core, "Config", lambda **kw: object())  # no OIDC discovery
    m2m = DatabricksConfig(host="h", http_path="/p", client_id="i", client_secret="s")
    assert "credentials_provider" in m2m.connect_kwargs() and m2m.auth_mode == "m2m"


# 4. separate jobs cap + cached language lookups -----------------------------


def test_jobs_max_env_is_separate_from_detail_cap(monkeypatch):
    """DATABRICKS_JOBS_MAX defaults to 2000 and does not follow DATABRICKS_DETAIL_MAX_TABLES."""
    monkeypatch.setenv("DATABRICKS_HOST", "h")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/p")
    monkeypatch.delenv("DATABRICKS_JOBS_MAX", raising=False)
    monkeypatch.setenv("DATABRICKS_DETAIL_MAX_TABLES", "5")
    cfg = DatabricksConfig.from_env()
    assert (cfg.detail_max_tables, cfg.jobs_max) == (5, 2000)
    monkeypatch.setenv("DATABRICKS_JOBS_MAX", "7")
    assert DatabricksConfig.from_env().jobs_max == 7


class _Rest(DatabricksSource):
    """Source whose REST calls are served from a script, recording each call."""

    def __init__(self, handler, **kw):
        super().__init__(_Conn(lambda s: []), **kw)
        self.calls: list[tuple[str, dict]] = []
        self.handler = handler

    def _rest(self, path, query):
        self.calls.append((path, dict(query)))
        return self.handler(path, query)


def _nb_job(i, path):
    return {"job_id": i, "settings": {"tasks": [{"notebook_task": {"notebook_path": path}}]}}


def test_job_cap_ignores_detail_cap_and_language_is_cached_per_path():
    """A low detail cap no longer truncates jobs; one notebook path costs one get-status call."""
    jobs = [_nb_job(i, "/shared/nb") for i in range(6)]

    def handler(path, q):
        if path.endswith("jobs/list"):
            return {"jobs": jobs}
        assert path.endswith("workspace/get-status")
        return {"language": "SQL"}

    src = _Rest(handler, detail_max_tables=1, jobs_max=100)
    table, partial = src.command(JOB_TASKS_COMMAND)
    assert partial is False and table.num_rows == 6
    assert [c for c in src.calls if "get-status" in c[0]] == [
        ("/api/2.0/workspace/get-status", {"path": "/shared/nb"})]
    assert set(table.column("file_language").to_pylist()) == {"sql"}


def test_job_task_rows_looks_up_each_path_once():
    """Repeated notebook paths reuse the first answer and do not use up the cap."""
    jobs = [_nb_job(i, "/same") for i in range(5)]
    calls = []
    rows, capped = job_task_rows(jobs, lambda p: calls.append(p) or "PYTHON", 1)
    assert calls == ["/same"] and capped is False and len(rows) == 5


# 5. per-job task pagination --------------------------------------------------


def _tasks(prefix, n):
    return [{"task_key": f"{prefix}{i}", "sql_task": {"warehouse_id": "w"}} for i in range(n)]


def test_job_with_has_more_pages_through_jobs_get():
    """A job flagged has_more has all its tasks fetched via jobs/get page tokens."""
    list_job = {"job_id": 9, "has_more": True, "settings": {"name": "big", "tasks": _tasks("a", 100)}}
    pages = {
        None: {"settings": {"tasks": _tasks("a", 100)}, "has_more": True, "next_page_token": "p2"},
        "p2": {"settings": {"tasks": _tasks("b", 30)}, "has_more": False},
    }

    def handler(path, q):
        if path.endswith("jobs/list"):
            return {"jobs": [list_job]}
        assert path == "/api/2.2/jobs/get" and q["job_id"] == 9
        return pages[q.get("page_token")]

    src = _Rest(handler)
    table, partial = src.command(JOB_TASKS_COMMAND)
    assert partial is False
    assert table.num_rows == 130
    assert [c[1].get("page_token") for c in src.calls if c[0].endswith("jobs/get")] == [None, "p2"]


def test_has_more_job_fetch_failure_marks_partial_and_keeps_first_page():
    """If jobs/get fails the first 100 tasks are kept and the extract is partial."""
    list_job = {"job_id": 9, "has_more": True, "settings": {"tasks": _tasks("a", 100)}}

    def handler(path, q):
        if path.endswith("jobs/list"):
            return {"jobs": [list_job]}
        raise RuntimeError("PERMISSION_DENIED: nope")

    table, partial = _Rest(handler).command(JOB_TASKS_COMMAND)
    assert partial is True and table.num_rows == 100


def test_jobs_without_has_more_make_no_extra_calls():
    """Jobs that fit in one page never trigger jobs/get."""
    def handler(path, q):
        assert path.endswith("jobs/list")
        return {"jobs": [{"job_id": 1, "has_more": False, "settings": {"tasks": _tasks("a", 3)}}]}

    src = _Rest(handler)
    table, partial = src.command(JOB_TASKS_COMMAND)
    assert partial is False and table.num_rows == 3 and len(src.calls) == 1
