"""Databricks SQL warehouse connection (databricks-sql-connector).

Configuration comes from the environment only:

- ``DATABRICKS_HOST``: workspace host, with or without ``https://``.
- ``DATABRICKS_HTTP_PATH`` (or ``DATABRICKS_WAREHOUSE_ID``): the SQL
  warehouse to run metadata queries on.
- Auth, first match wins:
  ``DATABRICKS_TOKEN`` (personal access token);
  ``DATABRICKS_CLIENT_ID`` + ``DATABRICKS_CLIENT_SECRET`` (OAuth M2M,
  service principal, needs the ``databricks-sdk`` package);
  otherwise browser OAuth (U2M) for interactive local runs.
- ``DATABRICKS_DETAIL_MAX_TABLES``: cap on per-table DESCRIBE DETAIL calls
  (default 2000); hitting it marks ``table_details`` partial.
- ``DATABRICKS_JOBS_MAX``: cap on jobs read and notebook get-status calls
  for ``job_tasks`` (default 2000), separate from the DESCRIBE DETAIL cap.
- ``DATABRICKS_WORKSPACES``: which workspaces the system tables (spend,
  queries, jobs, ...) cover. Unset or ``current``: only the workspace you
  connect to. ``all``: every workspace the account's system tables hold.
  Or a comma-separated list of workspace IDs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import pyarrow as pa

from ..base import SessionInfo
from .manifest import (
    HIVE_TABLE_COLUMNS,
    HIVE_TABLES_COMMAND,
    JOB_TASK_COLUMNS,
    JOB_TASKS_COMMAND,
    TABLE_DETAIL_COLUMNS,
    TABLE_DETAILS_COMMAND,
)

#: Catalogs never walked: platform furniture, the public sample data, the
#: workspace-local legacy metastore (its own planned signal), internal.
EXCLUDED_CATALOGS = frozenset({"system", "samples", "hive_metastore", "__databricks_internal"})

#: Hidden tables a materialized view / streaming table creates beside itself.
HIDDEN_TABLE_PREFIXES = ("__materialization_", "event_log_")

#: Table types DESCRIBE DETAIL accepts (views, MVs, streaming and foreign
#: tables are inventoried from information_schema only).
_DETAIL_TYPES = ("MANAGED", "EXTERNAL", "MANAGED_SHALLOW_CLONE", "EXTERNAL_SHALLOW_CLONE")

DEFAULT_DETAIL_MAX_TABLES = 2000

#: Default cap on jobs listed and notebook get-status calls (DATABRICKS_JOBS_MAX).
DEFAULT_JOBS_MAX = 2000

#: Most jobs/get pages read for one job's tasks before giving up (marks partial).
_MAX_TASK_PAGES = 50


#: Placeholder in system-table SQL. ``{workspace_filter}`` (or
#: ``{workspace_filter:alias}``) becomes "AND workspace_id IN (...)", or
#: nothing when every workspace is wanted.
_WORKSPACE_FILTER_RE = re.compile(r"\{workspace_filter(?::(\w+))?\}")


def parse_workspaces(value: str | None) -> str | tuple[str, ...]:
    """Read DATABRICKS_WORKSPACES: "current" (the default), "all", or a tuple of workspace IDs."""
    text = (value or "").strip().lower()
    if text in ("", "current"):
        return "current"
    if text == "all":
        return "all"
    ids = tuple(part.strip() for part in text.split(",") if part.strip())
    bad = [i for i in ids if not i.isdigit()]
    if bad or not ids:
        raise ValueError(
            "DATABRICKS_WORKSPACES must be 'current', 'all' or comma-separated "
            f"workspace IDs (numbers); got {value!r}"
        )
    return ids


def render_workspace_filter(sql: str, workspace_ids: tuple[str, ...] | None) -> str:
    """Limit a system-table query to the chosen workspaces. None means every workspace.

    Rows with no workspace (account-level spend such as default storage) are
    kept: they can't be split by workspace, and dropping them would undercount.
    """
    def sub(m: re.Match) -> str:
        """The filter for one placeholder, with the table alias if it has one."""
        if workspace_ids is None:
            return ""
        col = f"{m.group(1)}.workspace_id" if m.group(1) else "workspace_id"
        ids = ", ".join(repr(str(i)) for i in workspace_ids)
        return f"AND ({col} IN ({ids}) OR {col} IS NULL)"

    return _WORKSPACE_FILTER_RE.sub(sub, sql)


def bq(name: str) -> str:
    """Backtick-quote a Unity Catalog identifier."""
    return "`" + name.replace("`", "``") + "`"


@dataclass(frozen=True)
class DatabricksConfig:
    """Workspace address and login details, read from the environment."""
    host: str
    http_path: str
    token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    detail_max_tables: int = DEFAULT_DETAIL_MAX_TABLES
    jobs_max: int = DEFAULT_JOBS_MAX
    workspaces: str | tuple[str, ...] = "current"

    @classmethod
    def from_env(cls) -> "DatabricksConfig":
        """Build the config from DATABRICKS_* variables. Fail if the host or warehouse is missing."""
        env = os.environ
        host = (env.get("DATABRICKS_HOST") or "").strip()
        if not host:
            raise ValueError("DATABRICKS_HOST is not set")
        host = host.removeprefix("https://").removeprefix("http://").rstrip("/")
        http_path = (env.get("DATABRICKS_HTTP_PATH") or "").strip()
        if not http_path and env.get("DATABRICKS_WAREHOUSE_ID"):
            http_path = f"/sql/1.0/warehouses/{env['DATABRICKS_WAREHOUSE_ID'].strip()}"
        if not http_path:
            raise ValueError("set DATABRICKS_HTTP_PATH or DATABRICKS_WAREHOUSE_ID")
        return cls(
            host=host,
            http_path=http_path,
            token=env.get("DATABRICKS_TOKEN") or None,
            client_id=env.get("DATABRICKS_CLIENT_ID") or None,
            client_secret=env.get("DATABRICKS_CLIENT_SECRET") or None,
            detail_max_tables=int(
                env.get("DATABRICKS_DETAIL_MAX_TABLES") or DEFAULT_DETAIL_MAX_TABLES
            ),
            jobs_max=int(env.get("DATABRICKS_JOBS_MAX") or DEFAULT_JOBS_MAX),
            workspaces=parse_workspaces(env.get("DATABRICKS_WORKSPACES")),
        )

    @property
    def auth_mode(self) -> str:
        """Login method: "pat" token, "m2m" service principal, or "u2m" browser sign-in."""
        if self.token:
            return "pat"
        if self.client_id and self.client_secret:
            return "m2m"
        return "u2m"

    def u2m_sdk_config(self):
        """Build the one databricks-sdk Config for browser sign-in (U2M); SQL and REST both use it."""
        try:
            from databricks.sdk.core import Config
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise ImportError("browser sign-in needs databricks-sdk: pip install databricks-sdk") from exc
        return Config(host=f"https://{self.host}", auth_type="external-browser")

    def connect_kwargs(self, sdk_config=None) -> dict:
        """Arguments for the SQL connector that match the login method.

        In U2M mode, pass the shared SDK ``sdk_config`` so the SQL connector
        reuses its credentials and the browser opens only once."""
        kw: dict = {"server_hostname": self.host, "http_path": self.http_path}
        mode = self.auth_mode
        if mode == "pat":
            kw["access_token"] = self.token
        elif mode == "m2m":
            try:
                from databricks.sdk.core import Config, oauth_service_principal
            except ImportError as exc:  # pragma: no cover - environment-dependent
                raise ImportError(
                    "OAuth M2M needs databricks-sdk: pip install databricks-sdk"
                ) from exc
            cfg = Config(
                host=f"https://{self.host}",
                client_id=self.client_id,
                client_secret=self.client_secret,
            )
            kw["credentials_provider"] = lambda: oauth_service_principal(cfg)
        elif sdk_config is not None:
            kw["credentials_provider"] = lambda: sdk_config.authenticate
        else:
            kw["auth_type"] = "databricks-oauth"
        return kw


def _as_list(value) -> list:
    """Wrap one value in a list. None gives an empty list."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _as_dict(value) -> dict:
    """DESCRIBE DETAIL ``properties``: Arrow maps arrive as (k, v) tuples."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    return {k: v for k, v in value}


def detail_row(catalog: str, schema: str, table: str, detail: dict | None,
               error: str | None = None) -> dict:
    """Flatten one DESCRIBE DETAIL row into the table_details shape."""
    row = {c: None for c in TABLE_DETAIL_COLUMNS}
    row.update(table_catalog=catalog, table_schema=schema, table_name=table)
    if error is not None:
        row["detail_error"] = error[:500]
        return row
    d = detail or {}
    props = _as_dict(d.get("properties"))
    uniform = props.get("delta.universalFormat.enabledFormats")
    row.update(
        format=d.get("format"),
        location=d.get("location") or None,
        num_files=d.get("numFiles"),
        size_in_bytes=d.get("sizeInBytes"),
        partition_columns=",".join(_as_list(d.get("partitionColumns"))) or None,
        clustering_columns=",".join(_as_list(d.get("clusteringColumns"))) or None,
        table_features=",".join(sorted(_as_list(d.get("tableFeatures")))) or None,
        uniform_formats=uniform or None,
        cdf_enabled=str(props.get("delta.enableChangeDataFeed", "")).lower() == "true",
        dv_enabled=str(props.get("delta.enableDeletionVectors", "")).lower() == "true",
        column_mapping_mode=props.get("delta.columnMapping.mode") or None,
    )
    return row


def hive_tables_table(schemas, show_tables, cap: int) -> tuple[pa.Table, bool]:
    """Rows for raw.hive_metastore_tables; truncated past ``cap`` tables."""
    rows: list[dict] = []
    for schema in schemas:
        try:
            names = show_tables(schema)
        except Exception as exc:  # noqa: BLE001 - one schema must not sink the rest
            rows.append({"table_catalog": "hive_metastore", "table_schema": schema,
                         "table_name": None, "list_error": str(exc)})
            continue
        rows.extend({"table_catalog": "hive_metastore", "table_schema": schema,
                     "table_name": n, "list_error": None} for n in names)
    truncated = sum(r["table_name"] is not None for r in rows) > cap
    if truncated:
        kept, n = [], 0
        for r in rows:
            n += r["table_name"] is not None
            if n <= cap or r["table_name"] is None:
                kept.append(r)
        rows = kept
    schema = pa.schema([(c, pa.string()) for c in HIVE_TABLE_COLUMNS])
    return pa.Table.from_pylist(rows, schema=schema), truncated


#: Notebook/file extensions -> language, for Git-sourced code (the workspace
#: get-status call only answers for workspace objects).
_EXT_LANGUAGE = {".py": "python", ".ipynb": "python", ".sql": "sql",
                 ".scala": "scala", ".r": "r"}

#: Task-type keys of a Jobs API task, in the API's own naming, minus
#: the "_task" suffix (notebook, spark_python, dbt, sql, ...).
def _task_type(task: dict) -> str | None:
    """Type of a Jobs API task, such as "notebook" or "sql", or None if it has none."""
    keys = sorted(k for k in task if k.endswith("_task") and task[k] is not None)
    return keys[0].removesuffix("_task") if keys else None


def _trigger_kind(settings: dict) -> tuple[str, bool]:
    """How a job starts, and whether that trigger is paused."""
    if settings.get("continuous"):
        c = settings["continuous"]
        return "continuous", c.get("pause_status") == "PAUSED"
    trig = settings.get("trigger")
    if trig:
        kind = next((k for k in ("file_arrival", "table_update", "table", "periodic")
                     if trig.get(k)), "other")
        return ("table_update" if kind == "table" else kind,
                trig.get("pause_status") == "PAUSED")
    if settings.get("schedule"):
        return "cron", settings["schedule"].get("pause_status") == "PAUSED"
    return "manual", False


def _language_from_path(path: str | None) -> str | None:
    """Guess a notebook or file's language from its extension, or None."""
    if not path:
        return None
    lower = path.lower()
    return next((lang for ext, lang in _EXT_LANGUAGE.items() if lower.endswith(ext)), None)


def job_task_rows(jobs, notebook_language, cap: int) -> tuple[list[dict], bool]:
    """Flatten Jobs API job objects into raw.job_tasks rows.

    ``notebook_language(path)`` returns a workspace notebook's language
    (PYTHON/SQL/SCALA/R) or None; it is called at most ``cap`` times. Paths,
    task keys, parameters and code never reach a row: only the task type,
    language, compute kind, Spark version, library kinds and trigger.

    The flag is True when the rows are incomplete: the lookup cap was hit, or
    a lookup failed (that task's ``list_error`` says so, without the path).
    """
    rows: list[dict] = []
    lookups = 0
    incomplete = False
    seen: dict[str, str | None] = {}  # each notebook path is looked up once
    failed: set[str] = set()  # paths whose lookup raised
    for job in jobs:
        settings = job.get("settings") or {}
        trigger, paused = _trigger_kind(settings)
        clusters = {c.get("job_cluster_key"): (c.get("new_cluster") or {})
                    for c in settings.get("job_clusters") or []}
        git = bool(settings.get("git_source"))
        base = {"job_id": str(job.get("job_id")), "job_name": settings.get("name"),
                "trigger_kind": trigger, "trigger_paused": paused}
        for i, outer in enumerate(settings.get("tasks") or []):
            task, in_each = outer, False
            if outer.get("for_each_task"):
                task, in_each = outer["for_each_task"].get("task") or {}, True
            ttype = _task_type(task)
            body = task.get(f"{ttype}_task") or {} if ttype else {}
            source = body.get("source") or ("GIT" if git and ttype in (
                "notebook", "spark_python", "dbt", "sql") else
                "WORKSPACE" if ttype in ("notebook", "spark_python") else None)
            language = None
            list_error = None
            if ttype == "notebook":
                path = body.get("notebook_path")
                language = _language_from_path(path)
                if language is None and source != "GIT" and path:
                    if path in seen:
                        language = seen[path]
                    elif lookups < cap:
                        lookups += 1
                        try:
                            language = notebook_language(path)
                        except Exception:  # noqa: BLE001 - one notebook must not sink the rest
                            language = None
                            failed.add(path)
                        seen[path] = language
                    else:
                        incomplete = True
                    if path in failed:
                        # no exception text: it can carry the notebook path
                        list_error = "notebook language lookup failed"
                        incomplete = True
            elif ttype in ("spark_python", "python_wheel", "python_operator"):
                language = "python"
            elif ttype in ("spark_jar",):
                language = "jvm"
            elif ttype in ("sql", "dbt", "dbt_cloud", "dbt_platform"):
                language = "sql"
            if task.get("existing_cluster_id"):
                compute, spark = "all_purpose_cluster", None
            elif task.get("new_cluster") or task.get("job_cluster_key"):
                nc = task.get("new_cluster") or clusters.get(task.get("job_cluster_key"), {})
                compute, spark = "job_cluster", nc.get("spark_version")
            elif ttype == "sql":
                compute, spark = "sql_warehouse", None
            elif ttype == "pipeline":
                compute, spark = "pipeline", None
            elif ttype in ("run_job", "condition") or ttype is None:
                compute, spark = None, None
            else:
                compute, spark = "serverless", None
            kinds = sorted({k for lib in task.get("libraries") or [] for k in lib})
            rows.append({**base, "task_index": str(i), "task_type": ttype,
                         "in_for_each": in_each, "code_source": source,
                         "file_language": language.lower() if language else None,
                         "compute_kind": compute, "spark_version": spark,
                         "library_kinds": ",".join(kinds) or None,
                         "list_error": list_error})
    return rows, incomplete


def job_tasks_table(rows: list[dict]) -> pa.Table:
    """Arrow table of job task rows with fixed columns and types."""
    schema = pa.schema([
        (c, pa.bool_() if c in ("in_for_each", "trigger_paused") else pa.string())
        for c in JOB_TASK_COLUMNS
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def details_table(rows: list[dict]) -> pa.Table:
    """Arrow table of table detail rows with fixed columns and types."""
    schema = pa.schema([
        (c, pa.int64() if c in ("num_files", "size_in_bytes")
         else pa.bool_() if c in ("cdf_enabled", "dv_enabled")
         else pa.string())
        for c in TABLE_DETAIL_COLUMNS
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def _like_escape(text: str) -> str:
    """Escape LIKE wildcards (_ and %) in a literal prefix, for use with ESCAPE '!'.

    Not backslash: in Databricks SQL a backslash escapes the closing quote,
    so ESCAPE '\\' is an unterminated string there (it is fine in DuckDB).
    """
    return text.replace("!", "!!").replace("_", "!_").replace("%", "!%")


def detail_targets_sql(limit: int, scope_predicate: str = "") -> str:
    """Query for the tables to describe, limited to --scope before the cap.

    ``scope_predicate`` is the runner's SQL over table_catalog/table_schema.
    It asks for one more than the limit, so a cap can be detected."""
    excluded = ", ".join(f"'{c}'" for c in sorted(EXCLUDED_CATALOGS))
    types = ", ".join(f"'{t}'" for t in _DETAIL_TYPES)
    hidden = " AND ".join(
        f"table_name NOT LIKE '{_like_escape(p)}%' ESCAPE '!'" for p in HIDDEN_TABLE_PREFIXES
    )
    scope = f"AND ({scope_predicate}) " if scope_predicate else ""
    return (
        "SELECT table_catalog, table_schema, table_name "
        "FROM system.information_schema.tables "
        f"WHERE table_catalog NOT IN ({excluded}) "
        "AND table_schema <> 'information_schema' "
        f"AND table_type IN ({types}) AND {hidden} {scope}"
        "ORDER BY table_catalog, table_schema, table_name "
        f"LIMIT {int(limit) + 1}"
    )


class DatabricksSource:
    """Live Databricks SQL connection. Tests substitute a fake with the same shape."""

    def __init__(self, conn, detail_max_tables: int = DEFAULT_DETAIL_MAX_TABLES,
                 deployment: str | None = None, cfg: DatabricksConfig | None = None,
                 jobs_max: int | None = None, sdk_config=None) -> None:
        """Wrap a connection. The config is only needed for Jobs API calls; ``jobs_max`` caps job_tasks reads."""
        self._conn = conn
        self._detail_max_tables = detail_max_tables
        self._jobs_max = jobs_max if jobs_max is not None else (
            cfg.jobs_max if cfg else DEFAULT_JOBS_MAX)
        self._sdk_config = sdk_config
        self._scope_predicate = ""
        self._deployment = deployment
        self._cfg = cfg
        self._api = None
        self._api_ws = None
        self._workspace_ids: tuple[str, ...] | None | bool = False  # False = not looked up yet
        self._workspace_error: Exception | None = None

    @classmethod
    def open(cls, cfg: DatabricksConfig) -> "DatabricksSource":
        """Open a SQL warehouse connection from the config."""
        try:
            from databricks import sql as dbsql  # deferred: an optional extra
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise ImportError(
                "the Databricks SQL connector is not installed; install the "
                "'databricks' extra: pip install 'md-migration-assessment[databricks]'"
            ) from exc
        sdk_config = cfg.u2m_sdk_config() if cfg.auth_mode == "u2m" else None
        return cls(dbsql.connect(**cfg.connect_kwargs(sdk_config)), cfg.detail_max_tables,
                   cfg.host, cfg, sdk_config=sdk_config)

    def set_scope_predicate(self, predicate: str) -> None:
        """Set the --scope SQL predicate (over table_catalog/table_schema) applied before the DESCRIBE DETAIL cap."""
        self._scope_predicate = predicate or ""

    def _fetch(self, sql: str) -> pa.Table:
        """Run one statement and return its rows as an Arrow table."""
        with self._conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall_arrow()

    def reader(self, sql: str) -> pa.RecordBatchReader | pa.Table:
        """Run an extract query, limited to the chosen workspaces. The result comes back as one in-memory table."""
        if _WORKSPACE_FILTER_RE.search(sql):
            sql = render_workspace_filter(sql, self.workspace_ids())
        return self._fetch(sql)

    def workspace_ids(self) -> tuple[str, ...] | None:
        """The workspace IDs the system tables are limited to, or None for all.
        "current" asks Databricks which workspace we are connected to; if it
        can't tell, the system-table extracts fail with a clear message rather
        than silently counting every workspace."""
        if self._workspace_error is not None:
            raise self._workspace_error
        if self._workspace_ids is False:
            choice = self._cfg.workspaces if self._cfg else "all"
            if choice == "all":
                self._workspace_ids = None
            elif choice == "current":
                try:
                    self._rest_client()
                    self._workspace_ids = (str(self._api_ws.get_workspace_id()),)
                except Exception as exc:  # noqa: BLE001 - re-raised with advice
                    self._workspace_error = RuntimeError(
                        "could not find this workspace's ID; set DATABRICKS_WORKSPACES "
                        f"to the workspace ID, or to 'all': {exc}"
                    )
                    raise self._workspace_error from exc
            else:
                self._workspace_ids = tuple(choice)
        return self._workspace_ids

    def command(self, command: str) -> tuple[pa.Table, bool]:
        """Run an extract that is not one SQL query. Returns the rows and whether a cap cut them short."""
        if command == HIVE_TABLES_COMMAND:
            return self._hive_tables()
        if command == JOB_TASKS_COMMAND:
            return self._job_tasks()
        if command != TABLE_DETAILS_COMMAND:
            raise ValueError(f"unsupported Databricks command: {command!r}")
        cap = self._detail_max_tables
        targets = self._fetch(detail_targets_sql(cap, self._scope_predicate)).to_pylist()
        truncated = len(targets) > cap
        rows: list[dict] = []
        for t in targets[:cap]:
            cat, sch, tbl = t["table_catalog"], t["table_schema"], t["table_name"]
            try:
                detail = self._fetch(
                    f"DESCRIBE DETAIL {bq(cat)}.{bq(sch)}.{bq(tbl)}"
                ).to_pylist()
                rows.append(detail_row(cat, sch, tbl, detail[0] if detail else None))
            except Exception as exc:  # noqa: BLE001 - one table must not sink the rest
                rows.append(detail_row(cat, sch, tbl, None, error=str(exc)))
        return details_table(rows), truncated

    def _rest_client(self) -> None:
        """Sign in to the workspace REST API once, with the same login as SQL."""
        if self._api is None:
            if self._cfg is None:
                raise RuntimeError("PERMISSION_DENIED: no REST credentials for this connection")
            from databricks.sdk import WorkspaceClient  # deferred: the databricks extra

            cfg = self._cfg
            kw: dict = {"host": f"https://{cfg.host}"}
            if cfg.auth_mode == "pat":
                kw["token"] = cfg.token
            elif cfg.auth_mode == "m2m":
                kw.update(client_id=cfg.client_id, client_secret=cfg.client_secret)
            else:
                if self._sdk_config is None:
                    self._sdk_config = cfg.u2m_sdk_config()
                kw = {"config": self._sdk_config}  # the same sign-in the SQL connection uses
            self._api_ws = WorkspaceClient(**kw)
            self._api = self._api_ws.api_client

    def _rest(self, path: str, query: dict) -> dict:
        """GET a workspace REST endpoint with the same credentials as SQL.
        API errors are re-raised with their error code first, so the
        adapter's classifier sees PERMISSION_DENIED and friends."""
        self._rest_client()
        try:
            return self._api.do("GET", path, query=query) or {}
        except Exception as exc:  # normalized for classify_error, then re-raised
            code = getattr(exc, "error_code", None) or type(exc).__name__
            if type(exc).__name__ in ("PermissionDenied", "Unauthenticated"):
                code = "PERMISSION_DENIED"
            raise RuntimeError(f"{code}: {exc}") from exc

    def _expand_job(self, job: dict) -> bool:
        """Fetch all tasks and job clusters of a job whose arrays were cut at 100 (jobs/get, API 2.2).

        Updates ``job`` in place. Returns False if that failed, so the extract is marked partial."""
        try:
            tasks: list = []
            clusters: list = []
            token = None
            for _ in range(_MAX_TASK_PAGES):
                q = {"job_id": job.get("job_id")}
                if token:
                    q["page_token"] = token
                page = self._rest("/api/2.2/jobs/get", q)
                s = page.get("settings") or {}
                tasks.extend(s.get("tasks") or [])
                clusters.extend(s.get("job_clusters") or [])
                token = page.get("next_page_token")
                if not token:
                    settings = dict(job.get("settings") or {})
                    settings["tasks"], settings["job_clusters"] = tasks, clusters
                    job["settings"] = settings
                    job["has_more"] = False
                    return True
            return False
        except Exception:  # noqa: BLE001 - keep the first page of tasks, mark partial
            return False

    def _job_tasks(self) -> tuple[pa.Table, bool]:
        """Read jobs from the Jobs API and flatten their tasks.

        The job list and notebook get-status calls stop at DATABRICKS_JOBS_MAX; jobs with more
        than 100 tasks are paged through jobs/get. Any cut or failure marks the extract partial."""
        cap = self._jobs_max
        jobs: list[dict] = []
        token = None
        while True:
            q = {"expand_tasks": "true", "limit": 100}
            if token:
                q["page_token"] = token
            page = self._rest("/api/2.2/jobs/list", q)
            jobs.extend(page.get("jobs") or [])
            token = page.get("next_page_token")
            if not token or len(jobs) > cap:
                break
        truncated = len(jobs) > cap or bool(token)
        jobs = jobs[:cap]
        for job in jobs:
            if job.get("has_more") and not self._expand_job(job):
                truncated = True

        cache: dict[str, str | None] = {}

        def language(path: str) -> str | None:
            """Notebook language from the workspace (cached per path), or None."""
            if path not in cache:
                cache[path] = self._rest(
                    "/api/2.0/workspace/get-status", {"path": path}).get("language")
            return cache[path]

        rows, capped = job_task_rows(jobs, language, cap)
        return job_tasks_table(rows), truncated or capped

    def _hive_tables(self) -> tuple[pa.Table, bool]:
        """Walk the legacy Hive metastore. A disabled or inaccessible
        metastore raises here (classified unavailable); a schema that fails
        is one row with list_error, so it never sinks the rest."""
        schemas = self._fetch("SHOW SCHEMAS IN hive_metastore").column(0).to_pylist()
        return hive_tables_table(
            [s for s in schemas if s], self._show_tables, self._detail_max_tables
        )

    def _show_tables(self, schema: str) -> list[str]:
        """Names of the non-temporary tables in one legacy Hive schema."""
        t = self._fetch(f"SHOW TABLES IN hive_metastore.{bq(schema)}")
        temp = t.column("isTemporary").to_pylist() if "isTemporary" in t.column_names else None
        names = t.column("tableName").to_pylist()
        return [n for i, n in enumerate(names) if n and not (temp and temp[i])]

    def list_databases(self) -> list[str]:
        """Catalogs to walk, without the skipped system and sample ones."""
        names = self._fetch("SHOW CATALOGS").column(0).to_pylist()
        return [n for n in names if n and n.lower() not in EXCLUDED_CATALOGS]

    def session_info(self) -> SessionInfo:
        """Workspace host, SQL version and cloud:region of the metastore."""
        row = self._fetch(
            "SELECT current_metastore() AS metastore, "
            "current_version().dbsql_version AS version"
        ).to_pylist()[0]
        metastore = row.get("metastore") or ""
        # 'aws:us-east-2:<uuid>' -> cloud:region
        region = ":".join(metastore.split(":")[:2]) or None
        return SessionInfo(
            deployment=self._deployment, version=row.get("version"), region=region,
        )

    def server_time(self) -> datetime:
        """Databricks' own clock in UTC. Coverage windows are measured from it."""
        (us,) = self._fetch(
            "SELECT unix_micros(current_timestamp()) AS us"
        ).column(0).to_pylist()
        return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)

    def close(self) -> None:
        """Close the SQL connection."""
        self._conn.close()
