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
"""

from __future__ import annotations

import os
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


def bq(name: str) -> str:
    """Backtick-quote a Unity Catalog identifier."""
    return "`" + name.replace("`", "``") + "`"


@dataclass(frozen=True)
class DatabricksConfig:
    host: str
    http_path: str
    token: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    detail_max_tables: int = DEFAULT_DETAIL_MAX_TABLES

    @classmethod
    def from_env(cls) -> "DatabricksConfig":
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
        )

    @property
    def auth_mode(self) -> str:
        if self.token:
            return "pat"
        if self.client_id and self.client_secret:
            return "m2m"
        return "u2m"

    def connect_kwargs(self) -> dict:
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
        else:
            kw["auth_type"] = "databricks-oauth"
        return kw


def _as_list(value) -> list:
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
    """
    rows: list[dict] = []
    lookups = 0
    lookups_capped = False
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
            if ttype == "notebook":
                path = body.get("notebook_path")
                language = _language_from_path(path)
                if language is None and source != "GIT" and path:
                    if lookups < cap:
                        lookups += 1
                        try:
                            language = notebook_language(path)
                        except Exception:  # noqa: BLE001 - one notebook must not sink the rest
                            language = None
                    else:
                        lookups_capped = True
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
                         "library_kinds": ",".join(kinds) or None, "list_error": None})
    return rows, lookups_capped


def job_tasks_table(rows: list[dict]) -> pa.Table:
    schema = pa.schema([
        (c, pa.bool_() if c in ("in_for_each", "trigger_paused") else pa.string())
        for c in JOB_TASK_COLUMNS
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def details_table(rows: list[dict]) -> pa.Table:
    schema = pa.schema([
        (c, pa.int64() if c in ("num_files", "size_in_bytes")
         else pa.bool_() if c in ("cdf_enabled", "dv_enabled")
         else pa.string())
        for c in TABLE_DETAIL_COLUMNS
    ])
    return pa.Table.from_pylist(rows, schema=schema)


def detail_targets_sql(limit: int) -> str:
    excluded = ", ".join(f"'{c}'" for c in sorted(EXCLUDED_CATALOGS))
    types = ", ".join(f"'{t}'" for t in _DETAIL_TYPES)
    hidden = " AND ".join(f"table_name NOT LIKE '{p}%'" for p in HIDDEN_TABLE_PREFIXES)
    return (
        "SELECT table_catalog, table_schema, table_name "
        "FROM system.information_schema.tables "
        f"WHERE table_catalog NOT IN ({excluded}) "
        "AND table_schema <> 'information_schema' "
        f"AND table_type IN ({types}) AND {hidden} "
        "ORDER BY table_catalog, table_schema, table_name "
        f"LIMIT {int(limit) + 1}"
    )


class DatabricksSource:
    """Live Databricks SQL connection. Tests substitute a fake with the same shape."""

    def __init__(self, conn, detail_max_tables: int = DEFAULT_DETAIL_MAX_TABLES,
                 deployment: str | None = None, cfg: DatabricksConfig | None = None) -> None:
        self._conn = conn
        self._detail_max_tables = detail_max_tables
        self._deployment = deployment
        self._cfg = cfg
        self._api = None

    @classmethod
    def open(cls, cfg: DatabricksConfig) -> "DatabricksSource":
        try:
            from databricks import sql as dbsql  # deferred: an optional extra
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise ImportError(
                "the Databricks SQL connector is not installed; install the "
                "'databricks' extra: pip install 'md-migration-assessment[databricks]'"
            ) from exc
        return cls(dbsql.connect(**cfg.connect_kwargs()), cfg.detail_max_tables, cfg.host, cfg)

    def _fetch(self, sql: str) -> pa.Table:
        with self._conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall_arrow()

    def reader(self, sql: str) -> pa.RecordBatchReader | pa.Table:
        return self._fetch(sql)

    def command(self, command: str) -> tuple[pa.Table, bool]:
        if command == HIVE_TABLES_COMMAND:
            return self._hive_tables()
        if command == JOB_TASKS_COMMAND:
            return self._job_tasks()
        if command != TABLE_DETAILS_COMMAND:
            raise ValueError(f"unsupported Databricks command: {command!r}")
        cap = self._detail_max_tables
        targets = self._fetch(detail_targets_sql(cap)).to_pylist()
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

    def _rest(self, path: str, query: dict) -> dict:
        """GET a workspace REST endpoint with the same credentials as SQL.
        API errors are re-raised with their error code first, so the
        adapter's classifier sees PERMISSION_DENIED and friends."""
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
                kw["auth_type"] = "external-browser"
            self._api = WorkspaceClient(**kw).api_client
        try:
            return self._api.do("GET", path, query=query) or {}
        except Exception as exc:  # normalized for classify_error, then re-raised
            code = getattr(exc, "error_code", None) or type(exc).__name__
            if type(exc).__name__ in ("PermissionDenied", "Unauthenticated"):
                code = "PERMISSION_DENIED"
            raise RuntimeError(f"{code}: {exc}") from exc

    def _job_tasks(self) -> tuple[pa.Table, bool]:
        cap = self._detail_max_tables
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

        def language(path: str) -> str | None:
            return self._rest("/api/2.0/workspace/get-status", {"path": path}).get("language")

        rows, capped = job_task_rows(jobs[:cap], language, cap)
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
        t = self._fetch(f"SHOW TABLES IN hive_metastore.{bq(schema)}")
        temp = t.column("isTemporary").to_pylist() if "isTemporary" in t.column_names else None
        names = t.column("tableName").to_pylist()
        return [n for i, n in enumerate(names) if n and not (temp and temp[i])]

    def list_databases(self) -> list[str]:
        names = self._fetch("SHOW CATALOGS").column(0).to_pylist()
        return [n for n in names if n and n.lower() not in EXCLUDED_CATALOGS]

    def session_info(self) -> SessionInfo:
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
        (us,) = self._fetch(
            "SELECT unix_micros(current_timestamp()) AS us"
        ).column(0).to_pylist()
        return datetime.fromtimestamp(us / 1_000_000, tz=timezone.utc)

    def close(self) -> None:
        self._conn.close()
