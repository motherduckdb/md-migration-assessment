"""Realistic raw-evidence fixtures: one arrow table per extractor, containing
exactly one exemplar of each feature signal (plus a SNOWFLAKE-system row that
must be excluded from counts)."""

from __future__ import annotations

import pyarrow as pa


def _t(rows: list[dict]) -> pa.Table:
    cols: dict[str, list] = {}
    for row in rows:
        for k in row:
            cols.setdefault(k, [])
    for row in rows:
        for k in cols:
            cols[k].append(row.get(k))
    return pa.table(cols)


def _tbl(name, schema="S1", catalog="APPDB", **kw) -> dict:
    base = dict(
        table_catalog=catalog, table_schema=schema, table_name=name,
        table_owner="OWNER", table_type="BASE TABLE", is_transient="NO",
        is_iceberg="NO", is_dynamic="NO", is_hybrid="NO", clustering_key=None,
        auto_clustering_on="NO", row_count=10, bytes=1000, retention_time=1,
        comment=None,
    )
    base.update(kw)
    return base


def _col(table, column, data_type, precision=None, catalog="APPDB") -> dict:
    return dict(
        table_catalog=catalog, table_schema="S1", table_name=table,
        column_name=column, ordinal_position=1, column_default=None,
        is_nullable="YES", data_type=data_type, datetime_precision=precision,
        comment=None,
    )


REALISTIC = {
    "databases": _t([
        dict(database_name="APPDB", database_owner="OWNER", is_transient="NO",
             retention_time=1, comment=None),
    ]),
    "schemata": _t([
        dict(catalog_name="APPDB", schema_name="S1", schema_owner="OWNER",
             is_transient="NO", is_managed_access="NO", retention_time=1,
             comment=None),
    ]),
    "tables": _t([
        _tbl("PLAIN"),
        _tbl("TRANSIENT_T", is_transient="YES"),
        _tbl("ICEBERG_T", is_iceberg="YES"),
        _tbl("DYNAMIC_T", is_dynamic="YES"),
        _tbl("HYBRID_T", is_hybrid="YES"),
        _tbl("MV_T", table_type="MATERIALIZED VIEW"),
        _tbl("CLUSTERED_COL", clustering_key="LINEAR(EVENT_DATE, REGION)"),
        _tbl("CLUSTERED_EXPR", clustering_key="LINEAR(to_date(created_at))",
             auto_clustering_on="YES"),
        _tbl("TT_10D", retention_time=10),
        _tbl("TT_30D", retention_time=30),
        _tbl("SYS_T", catalog="SNOWFLAKE", is_transient="YES"),  # excluded
    ]),
    "columns": _t([
        _col("PLAIN", "ID", "NUMBER"),
        _col("PLAIN", "PAYLOAD", "VARIANT"),
        _col("PLAIN", "META", "OBJECT"),
        _col("PLAIN", "TAGS_A", "ARRAY"),
        _col("PLAIN", "LOC", "GEOGRAPHY"),
        _col("PLAIN", "EMB", "VECTOR(FLOAT, 768)"),
        _col("PLAIN", "TS_TZ", "TIMESTAMP_TZ", 6),
        _col("PLAIN", "TS_LTZ", "TIMESTAMP_LTZ", 6),
        _col("PLAIN", "TS_NANO", "TIMESTAMP_NTZ", 9),
        _col("SYS_T", "V", "VARIANT", catalog="SNOWFLAKE"),  # excluded
    ]),
    "views": _t([
        dict(table_catalog="APPDB", table_schema="S1", table_name="V_PLAIN",
             table_owner="OWNER", view_definition="CREATE VIEW ...",
             is_secure="NO", comment=None),
        dict(table_catalog="APPDB", table_schema="S1", table_name="V_SECURE",
             table_owner="OWNER", view_definition="CREATE SECURE VIEW ...",
             is_secure="YES", comment=None),
    ]),
    "functions": _t([
        dict(function_catalog="APPDB", function_schema="S1", function_name="F_SQL",
             function_language="SQL", is_external="NO", packages=None),
        dict(function_catalog="APPDB", function_schema="S1", function_name="F_JS",
             function_language="JAVASCRIPT", is_external="NO", packages=None),
        dict(function_catalog="APPDB", function_schema="S1", function_name="F_PY",
             function_language="PYTHON", is_external="NO",
             packages='["snowflake-snowpark-python"]'),
        dict(function_catalog="APPDB", function_schema="S1", function_name="F_EXT",
             function_language="JAVA", is_external="YES", packages=None),
    ]),
    "procedures": _t([
        dict(procedure_catalog="APPDB", procedure_schema="S1",
             procedure_name="P_SQL", procedure_language="SQL"),
        dict(procedure_catalog="APPDB", procedure_schema="S1",
             procedure_name="P_JS", procedure_language="JAVASCRIPT"),
        dict(procedure_catalog="APPDB", procedure_schema="S1",
             procedure_name="P_PY", procedure_language="PYTHON"),
    ]),
    "table_storage_metrics": _t([
        dict(table_catalog="APPDB", table_schema="S1", table_name="PLAIN",
             id=1, clone_group_id=1, active_bytes=1000, time_travel_bytes=10,
             failsafe_bytes=0, retained_for_clone_bytes=0),
        dict(table_catalog="APPDB", table_schema="S1", table_name="CLONE_T",
             id=2, clone_group_id=1, active_bytes=0, time_travel_bytes=0,
             failsafe_bytes=0, retained_for_clone_bytes=500),
    ]),
    "stage_storage_usage_history": _t([
        dict(usage_date="2026-08-01", average_stage_bytes=100.0),
    ]),
    "database_storage_usage_history": _t([
        dict(usage_date="2026-08-01", database_name="APPDB",
             average_database_bytes=1000.0, average_failsafe_bytes=0.0),
    ]),
    "masking_policies": _t([
        dict(policy_catalog="APPDB", policy_schema="S1", policy_name="MASK_EMAIL",
             policy_owner="OWNER", policy_body="CASE WHEN ...", policy_comment=None),
    ]),
    "row_access_policies": _t([
        dict(policy_catalog="APPDB", policy_schema="S1", policy_name="RAP_REGION",
             policy_owner="OWNER", policy_body="region = ...", policy_comment=None),
    ]),
    "policy_references": _t([
        dict(policy_db="APPDB", policy_schema="S1", policy_name="MASK_EMAIL",
             policy_kind="MASKING_POLICY", ref_database_name="APPDB",
             ref_schema_name="S1", ref_entity_name="PLAIN",
             ref_entity_domain="TABLE", ref_column_name="EMAIL"),
        dict(policy_db="APPDB", policy_schema="S1", policy_name="RAP_REGION",
             policy_kind="ROW_ACCESS_POLICY", ref_database_name="APPDB",
             ref_schema_name="S1", ref_entity_name="PLAIN",
             ref_entity_domain="TABLE", ref_column_name=None),
    ]),
    "tags": _t([
        dict(tag_database="APPDB", tag_schema="S1", tag_name="PII",
             tag_owner="OWNER", allowed_values=None, tag_comment=None),
    ]),
    "tag_references": _t([
        dict(tag_database="APPDB", tag_schema="S1", tag_name="PII",
             tag_value="high", object_database="APPDB", object_schema="S1",
             object_name="PLAIN", domain="TABLE", column_name=None),
    ]),
    "pipes": _t([
        dict(pipe_catalog="APPDB", pipe_schema="S1", pipe_name="LOAD_EVENTS",
             pipe_owner="OWNER", is_autoingest_enabled="YES",
             definition="COPY INTO ...", comment=None),
        dict(pipe_catalog="APPDB", pipe_schema="S1", pipe_name="LOAD_MANUAL",
             pipe_owner="OWNER", is_autoingest_enabled="NO",
             definition="COPY INTO ...", comment=None),
    ]),
    "roles": _t([
        dict(name="ACCOUNTADMIN", owner=None, role_type="ROLE", comment=None),
        dict(name="ANALYST", owner="SECURITYADMIN", role_type="ROLE", comment=None),
        dict(name="DB_ROLE", owner="X", role_type="DATABASE_ROLE", comment=None),
    ]),
}
