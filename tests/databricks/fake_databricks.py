"""Synthetic Unity Catalog for adapter tests.

The fake runs the adapter's real extract SQL in DuckDB: references to
``<catalog>.information_schema.<view>`` and ``system.information_schema.<view>``
are rewritten to in-memory tables, so the SQL files, scope predicates and
quoting are all exercised. The estate mirrors the synthetic ``assess_lab``
seed catalog (no customer data anywhere).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import duckdb
import pyarrow as pa

from md_migration_assessment.sources.base import SessionInfo
from md_migration_assessment.sources.databricks.connection import (
    detail_row,
    details_table,
    hive_tables_table,
)
from md_migration_assessment.sources.databricks.manifest import (
    HIVE_TABLES_COMMAND,
    TABLE_DETAILS_COMMAND,
)

#: legacy Hive metastore: schema -> tables (None = SHOW TABLES fails there)
HIVE = {"legacy_sales": ["orders_2019", "customers_2019"], "locked": None}

SERVER_ANCHOR = datetime(2030, 1, 1, tzinfo=timezone.utc)

#: per-catalog view -> its catalog column
CATALOG_COL = {
    "schemata": "catalog_name", "tables": "table_catalog", "columns": "table_catalog",
    "views": "table_catalog", "routines": "routine_catalog",
    "table_constraints": "table_catalog", "column_masks": "table_catalog",
    "row_filters": "table_catalog", "table_tags": "catalog_name",
    "column_tags": "catalog_name", "volumes": "volume_catalog",
}

_T = "created TIMESTAMP DEFAULT TIMESTAMP '2026-11-05', created_by VARCHAR DEFAULT 'seed@example.com', last_altered TIMESTAMP DEFAULT TIMESTAMP '2026-11-05', comment VARCHAR"

DDL = f"""
CREATE TABLE catalogs (catalog_name VARCHAR, catalog_owner VARCHAR, {_T});
CREATE TABLE schemata (catalog_name VARCHAR, schema_name VARCHAR, schema_owner VARCHAR, {_T});
CREATE TABLE tables (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,
    table_type VARCHAR, data_source_format VARCHAR, storage_path VARCHAR, table_owner VARCHAR, {_T});
CREATE TABLE columns (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,
    column_name VARCHAR, ordinal_position INT, data_type VARCHAR, full_data_type VARCHAR,
    is_nullable VARCHAR, partition_index INT, comment VARCHAR);
CREATE TABLE views (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,
    is_materialized BOOLEAN, view_definition VARCHAR);
CREATE TABLE routines (routine_catalog VARCHAR, routine_schema VARCHAR, routine_name VARCHAR,
    routine_type VARCHAR, routine_body VARCHAR, external_language VARCHAR, data_type VARCHAR,
    is_deterministic VARCHAR, security_type VARCHAR, routine_owner VARCHAR, created TIMESTAMP,
    last_altered TIMESTAMP, routine_definition VARCHAR, comment VARCHAR);
CREATE TABLE table_constraints (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,
    constraint_name VARCHAR, constraint_type VARCHAR, enforced VARCHAR);
CREATE TABLE column_masks (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,
    column_name VARCHAR, mask_name VARCHAR, using_columns VARCHAR);
CREATE TABLE row_filters (table_catalog VARCHAR, table_schema VARCHAR, table_name VARCHAR,
    filter_name VARCHAR, target_columns VARCHAR);
CREATE TABLE table_tags (catalog_name VARCHAR, schema_name VARCHAR, table_name VARCHAR,
    tag_name VARCHAR, tag_value VARCHAR);
CREATE TABLE column_tags (catalog_name VARCHAR, schema_name VARCHAR, table_name VARCHAR,
    column_name VARCHAR, tag_name VARCHAR, tag_value VARCHAR);
CREATE TABLE volumes (volume_catalog VARCHAR, volume_schema VARCHAR, volume_name VARCHAR,
    volume_type VARCHAR, storage_location VARCHAR, volume_owner VARCHAR, created TIMESTAMP, comment VARCHAR);
CREATE TABLE metastores (metastore_name VARCHAR, cloud VARCHAR, region VARCHAR,
    privilege_model_version VARCHAR, delta_sharing_scope VARCHAR);
CREATE TABLE external_locations (external_location_name VARCHAR, url VARCHAR,
    storage_credential_name VARCHAR, read_only BOOLEAN, external_location_owner VARCHAR,
    created TIMESTAMP, comment VARCHAR);
CREATE TABLE storage_credentials (storage_credential_name VARCHAR, credential_type VARCHAR,
    used_for_managed_storage BOOLEAN, storage_credential_owner VARCHAR, created TIMESTAMP,
    comment VARCHAR, credential VARCHAR);
CREATE TABLE connections (connection_name VARCHAR, connection_type VARCHAR,
    credential_type VARCHAR, connection_owner VARCHAR, created TIMESTAMP, comment VARCHAR,
    connection_options VARCHAR, url VARCHAR);
CREATE TABLE shares (share_name VARCHAR, share_owner VARCHAR, created TIMESTAMP, comment VARCHAR);
"""

S3 = "s3://synthetic-bucket/uc"
OWNER = "seed@example.com"

SEED = f"""
INSERT INTO catalogs (catalog_name, catalog_owner) VALUES
  ('assess_lab', '{OWNER}'), ('workspace', '{OWNER}'), ('system', 'System user');
INSERT INTO schemata (catalog_name, schema_name, schema_owner) VALUES
  ('assess_lab', 'core', '{OWNER}'), ('assess_lab', 'raw', '{OWNER}'),
  ('assess_lab', 'marts', '{OWNER}'), ('assess_lab', 'sandbox', '{OWNER}'),
  ('assess_lab', 'information_schema', 'System user'), ('workspace', 'default', '{OWNER}');
INSERT INTO tables (table_catalog, table_schema, table_name, table_type, data_source_format, storage_path, table_owner) VALUES
  ('assess_lab', 'core', 'customers', 'MANAGED', 'DELTA', '{S3}/customers', '{OWNER}'),
  ('assess_lab', 'core', 'products', 'MANAGED', 'DELTA', '{S3}/products', '{OWNER}'),
  ('assess_lab', 'core', 'orders', 'MANAGED', 'DELTA', '{S3}/orders', '{OWNER}'),
  ('assess_lab', 'core', 'order_items', 'MANAGED', 'DELTA', '{S3}/order_items', '{OWNER}'),
  ('assess_lab', 'raw', 'events', 'MANAGED', 'DELTA', '{S3}/events', '{OWNER}'),
  ('assess_lab', 'raw', 'ext_clicks', 'EXTERNAL', 'DELTA', 's3://synthetic-ext/clicks', '{OWNER}'),
  ('assess_lab', 'marts', 'region_targets', 'MANAGED', 'ICEBERG', '{S3}/region_targets', '{OWNER}'),
  ('assess_lab', 'marts', 'customer_ltv', 'VIEW', 'UNKNOWN_DATA_SOURCE_FORMAT', NULL, '{OWNER}'),
  ('assess_lab', 'marts', 'daily_revenue', 'MATERIALIZED_VIEW', 'UNKNOWN_DATA_SOURCE_FORMAT', NULL, '{OWNER}'),
  ('assess_lab', 'marts', '__materialization_mat_daily_revenue_1', 'MANAGED', 'DELTA', '{S3}/m', '{OWNER}'),
  ('assess_lab', 'marts', 'event_log_0123', 'MANAGED', 'DELTA', '{S3}/e', '{OWNER}'),
  ('assess_lab', 'sandbox', 'tmp_orders_backup', 'MANAGED', 'DELTA', '{S3}/tmp', '{OWNER}'),
  ('assess_lab', 'sandbox', 'events_shallow', 'MANAGED_SHALLOW_CLONE', 'DELTA', '{S3}/sc', '{OWNER}'),
  ('assess_lab', 'information_schema', 'tables', 'VIEW', NULL, NULL, 'System user'),
  ('workspace', 'default', 'assess_scope_probe', 'MANAGED', 'DELTA', '{S3}/probe', '{OWNER}');
INSERT INTO columns (table_catalog, table_schema, table_name, column_name, ordinal_position, data_type) VALUES
  ('assess_lab', 'core', 'customers', 'customer_id', 0, 'BIGINT'),
  ('assess_lab', 'core', 'customers', 'email', 1, 'STRING'),
  ('assess_lab', 'core', 'orders', 'region', 3, 'STRING'),
  ('assess_lab', 'raw', 'events', 'payload', 2, 'VARIANT'),
  ('assess_lab', 'raw', 'events', 'attrs', 3, 'MAP'),
  ('workspace', 'default', 'assess_scope_probe', 'id', 0, 'INT');
INSERT INTO views VALUES
  ('assess_lab', 'marts', 'customer_ltv', false, 'SELECT customer_id, sum(amount) FROM core.orders GROUP BY 1'),
  ('assess_lab', 'marts', 'daily_revenue', true, 'SELECT order_date, sum(amount) FROM core.orders GROUP BY 1');
INSERT INTO routines (routine_catalog, routine_schema, routine_name, routine_type, routine_body, external_language, routine_owner, routine_definition) VALUES
  ('assess_lab', 'core', 'net_amount', 'FUNCTION', 'SQL', NULL, '{OWNER}', 'amount - discount'),
  ('assess_lab', 'core', 'email_domain', 'FUNCTION', 'EXTERNAL', 'Python', '{OWNER}', 'return e.split("@")[1]'),
  ('assess_lab', 'core', 'mask_email', 'FUNCTION', 'SQL', NULL, '{OWNER}', 'CASE WHEN ... END'),
  ('assess_lab', 'core', 'region_filter', 'FUNCTION', 'SQL', NULL, '{OWNER}', 'region = ''NA''');
INSERT INTO table_constraints VALUES
  ('assess_lab', 'core', 'customers', 'customers_pk', 'PRIMARY KEY', 'NO'),
  ('assess_lab', 'core', 'orders', 'orders_customer_fk', 'FOREIGN KEY', 'NO');
INSERT INTO column_masks VALUES ('assess_lab', 'core', 'customers', 'email', 'assess_lab.core.mask_email', NULL);
INSERT INTO row_filters VALUES ('assess_lab', 'core', 'orders', 'assess_lab.core.region_filter', 'region');
INSERT INTO table_tags VALUES ('assess_lab', 'core', 'customers', 'pii', 'true');
INSERT INTO column_tags VALUES ('assess_lab', 'core', 'customers', 'email', 'pii', 'email');
INSERT INTO volumes (volume_catalog, volume_schema, volume_name, volume_type, storage_location, volume_owner) VALUES
  ('assess_lab', 'raw', 'landing', 'MANAGED', '{S3}/volumes/landing', '{OWNER}');
INSERT INTO metastores VALUES ('metastore_aws_us_east_2', 'aws', 'us-east-2', '1.0', 'INTERNAL');
INSERT INTO storage_credentials VALUES ('managed_cred', 'AWS_IAM_ROLE', true, '{OWNER}', NULL, NULL, 'arn:aws:iam::000000000000:role/secret');
INSERT INTO connections VALUES ('pg_fed', 'POSTGRESQL', 'USERNAME_PASSWORD', '{OWNER}', NULL, NULL, 'host=secret', 'jdbc:secret');
"""


def _detail(fmt="delta", parts=(), clust=(), feats=(), props=None):
    return {
        "format": fmt, "location": None, "numFiles": 4, "sizeInBytes": 1000,
        "partitionColumns": list(parts), "clusteringColumns": list(clust),
        "tableFeatures": list(feats), "properties": list((props or {}).items()),
    }


DETAILS = {
    ("assess_lab", "core", "customers"): _detail(
        feats=("deletionVectors", "identityColumns", "columnMapping"),
        props={"delta.enableDeletionVectors": "true"}),
    ("assess_lab", "core", "products"): _detail(feats=("generatedColumns",)),
    ("assess_lab", "core", "orders"): _detail(clust=("order_date",), feats=("clustering",)),
    ("assess_lab", "core", "order_items"): _detail(
        feats=("icebergCompatV2",),
        props={"delta.universalFormat.enabledFormats": "iceberg"}),
    ("assess_lab", "raw", "events"): _detail(
        parts=("event_date",), feats=("changeDataFeed",),
        props={"delta.enableChangeDataFeed": "true"}),
    ("assess_lab", "raw", "ext_clicks"): _detail(),
    ("assess_lab", "marts", "region_targets"): _detail(fmt="iceberg"),
    ("assess_lab", "sandbox", "tmp_orders_backup"): _detail(
        feats=("columnMapping",), props={"delta.columnMapping.mode": "name"}),
    ("assess_lab", "sandbox", "events_shallow"): _detail(),
    ("workspace", "default", "assess_scope_probe"): _detail(),
}

#: system.query.history: a few statements inside the 30-day window ending at
#: SERVER_ANCHOR and one outside it. Synthetic, aggregate-only in the extract.
WORKLOAD_DDL = """
CREATE TABLE query_history (
    compute STRUCT(type VARCHAR, warehouse_id VARCHAR), statement_type VARCHAR,
    execution_status VARCHAR, start_time TIMESTAMP, from_result_cache BOOLEAN,
    total_duration_ms BIGINT, read_bytes BIGINT, read_rows BIGINT,
    written_bytes BIGINT, spilled_local_bytes BIGINT,
    waiting_at_capacity_duration_ms BIGINT, waiting_for_compute_duration_ms BIGINT,
    statement_text VARCHAR, executed_by VARCHAR
);
INSERT INTO query_history VALUES
  ({'type': 'WAREHOUSE', 'warehouse_id': 'wh1'}, 'SELECT', 'FINISHED', TIMESTAMP '2029-12-30 10:00', false, 100, 1000, 10, 0, 0, 0, 5, 'select 1', 'seed@example.com'),
  ({'type': 'WAREHOUSE', 'warehouse_id': 'wh1'}, 'SELECT', 'FAILED',   TIMESTAMP '2029-12-30 11:00', false, 50, 0, 0, 0, 0, 0, 0, 'select x', 'seed@example.com'),
  ({'type': 'WAREHOUSE', 'warehouse_id': 'wh1'}, 'SELECT', 'FINISHED', TIMESTAMP '2029-12-31 09:00', true, 5, 0, 0, 0, 0, 0, 0, 'select 1', 'seed@example.com'),
  ({'type': 'SERVERLESS_COMPUTE', 'warehouse_id': NULL}, 'REPLACE', 'FINISHED', TIMESTAMP '2029-12-31 12:00', false, 900, 0, 0, 5000, 0, NULL, NULL, 'create or replace', 'seed@example.com'),
  ({'type': 'WAREHOUSE', 'warehouse_id': 'wh1'}, 'SELECT', 'FINISHED', TIMESTAMP '2029-10-01 00:00', false, 1, 0, 0, 0, 0, 0, 0, 'old', 'seed@example.com');
CREATE MACRO percentile_approx(x, p) AS quantile_disc(x, p);
"""

_PER_CAT_RE = re.compile(r"`((?:[^`]|``)+)`\.information_schema\.(\w+)")
_SYSTEM_RE = re.compile(r"system\.information_schema\.(\w+)")


class FakeDatabricks:
    def __init__(self, *, denied_views: set[str] = frozenset(),
                 denied_catalogs: set[str] = frozenset(),
                 detail_errors: dict | None = None,
                 hive: dict | None = HIVE, hive_cap: int = 2000) -> None:
        self.db = duckdb.connect()
        self.db.execute(DDL)
        self.db.execute(SEED)
        self.db.execute(WORKLOAD_DDL)
        self.denied_views = set(denied_views)
        self.denied_catalogs = set(denied_catalogs)
        self.detail_errors = detail_errors or {}
        self.hive = hive
        self.hive_cap = hive_cap
        self.queries: list[str] = []
        self.closed = False

    def _rewrite(self, sql: str) -> str:
        def per_cat(m):
            cat, view = m.group(1).replace("``", "`"), m.group(2)
            if view in self.denied_views or cat in self.denied_catalogs:
                raise PermissionError(
                    f"[INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG on Catalog '{cat}'."
                )
            return f"(SELECT * FROM {view} WHERE {CATALOG_COL[view]} = '{cat}')"

        def system(m):
            view = m.group(1)
            if view in self.denied_views:
                raise PermissionError("PERMISSION_DENIED: metastore admin required")
            return view

        if "system.query.history" in sql:
            if "query_history" in self.denied_views:
                raise PermissionError(
                    "[INSUFFICIENT_PERMISSIONS] User does not have USE SCHEMA on Schema 'system.query'."
                )
            sql = sql.replace("system.query.history", "query_history").replace(
                "current_timestamp()", f"TIMESTAMP '{SERVER_ANCHOR:%Y-%m-%d %H:%M:%S}'"
            )
        return _SYSTEM_RE.sub(system, _PER_CAT_RE.sub(per_cat, sql))

    def reader(self, sql: str) -> pa.Table:
        self.queries.append(sql)
        return self.db.execute(self._rewrite(sql)).arrow()

    def command(self, command: str) -> tuple[pa.Table, bool]:
        if command == HIVE_TABLES_COMMAND:
            if self.hive is None:
                raise RuntimeError(
                    "[UC_HIVE_METASTORE_DISABLED_EXCEPTION] The operation attempted "
                    "to use Hive Metastore, which is disabled. SQLSTATE: 0A000"
                )

            def show_tables(schema):
                if self.hive[schema] is None:
                    raise PermissionError("[INSUFFICIENT_PERMISSIONS] no USAGE on schema")
                return self.hive[schema]

            return hive_tables_table(list(self.hive), show_tables, self.hive_cap)
        assert command == TABLE_DETAILS_COMMAND, command
        rows = []
        for (cat, sch, tbl), d in sorted(DETAILS.items()):
            err = self.detail_errors.get((cat, sch, tbl))
            rows.append(detail_row(cat, sch, tbl, None if err else d, error=err))
        return details_table(rows), False

    def list_databases(self) -> list[str]:
        return ["assess_lab", "workspace"]

    def session_info(self) -> SessionInfo:
        return SessionInfo("dbc-test.cloud.databricks.com", "2026.43", "aws:us-east-2")

    def server_time(self) -> datetime:
        return SERVER_ANCHOR

    def close(self) -> None:
        self.closed = True
