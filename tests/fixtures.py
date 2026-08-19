"""Realistic raw-evidence fixtures: one arrow table per extractor, containing
exactly one exemplar of each feature signal (plus a SNOWFLAKE-system row that
must be excluded from counts)."""

from __future__ import annotations

from datetime import date, datetime

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
        column_name=column, ordinal_position=1, column_default="0 /* body */",
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
             function_language="SQL", is_external="NO", packages=None,
             function_definition="a + b"),
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
             procedure_name="P_SQL", procedure_language="SQL",
             procedure_definition="BEGIN RETURN 1; END"),
        dict(procedure_catalog="APPDB", procedure_schema="S1",
             procedure_name="P_JS", procedure_language="JAVASCRIPT"),
        dict(procedure_catalog="APPDB", procedure_schema="S1",
             procedure_name="P_PY", procedure_language="PYTHON"),
        dict(procedure_catalog="APPDB", procedure_schema="S1",
             procedure_name="P_CURSOR", procedure_language="SQL",
             procedure_definition="DECLARE c1 CURSOR FOR SELECT * FROM t; BEGIN OPEN c1; END"),
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
    "tasks": _t([
        dict(task_database="APPDB", task_schema="S1", task_name="NIGHTLY_ROLLUP",
             task_owner="OWNER", warehouse="WH1", schedule="1440 MINUTE",
             state="started", definition="INSERT INTO ...", condition=None,
             comment=None),
    ]),
    "stages": _t([
        dict(stage_catalog="APPDB", stage_schema="S1", stage_name="S3_LANDING",
             stage_owner="OWNER", stage_url="s3://bucket/path",
             stage_type="External Named", comment=None),
        dict(stage_catalog="APPDB", stage_schema="S1", stage_name="INT_STAGE",
             stage_owner="OWNER", stage_url=None,
             stage_type="Internal Named", comment=None),
    ]),
    "shares": _t([
        dict(name="OUT_SHARE", owner="OWNER", database_name="APPDB",
             secure_objects_only="true", target_accounts="PARTNERORG.ACCT1",
             listing_global_name=None, comment=None),
    ]),
    "listings": _t([
        dict(name="PARTNER_SHARE", global_name=None, owner="OWNER",
             title="Partner data share", state="PUBLISHED", is_share=True,
             is_application=False, share="PARTNER_SHARE_OBJ"),
    ]),
    "external_tables": _t([
        dict(table_catalog="APPDB", table_schema="S1", table_name="EXT_EVENTS",
             table_owner="OWNER", location="s3://bucket/ext/",
             file_format_name="PQ", file_format_type="PARQUET", comment=None),
    ]),
    "cortex_ai_functions_usage_history": _t([
        dict(function_name="COMPLETE", model_name="mistral-large",
             n_queries=42, total_credits=1.5),
    ]),
    "search_optimization_history": _t([
        dict(database_name="APPDB", schema_name="S1", table_name="PLAIN",
             n_operations=12, total_credits=0.2),
    ]),
    "snowpipe_streaming_client_history": _t([
        dict(client_name="KAFKA_CONNECTOR_1", n_events=100,
             total_blob_bytes=1000000),
    ]),
    "roles": _t([
        dict(name="ACCOUNTADMIN", owner=None, role_type="ROLE", comment=None),
        dict(name="ANALYST", owner="SECURITYADMIN", role_type="ROLE", comment=None),
        dict(name="DB_ROLE", owner="X", role_type="DATABASE_ROLE", comment=None),
    ]),
}


def _ts(hour: int, day: int = 10) -> datetime:
    return datetime(2026, 8, day, hour, 0, 0)


#: M3b workload extracts (full profile only): shapes mirror the aggregated
#: extract SQL projections, not the underlying Snowflake views.
WORKLOAD = {
    "warehouse_metering_history": _t([
        dict(start_time=_ts(9), end_time=_ts(10), warehouse_name="ETL_WH",
             credits_used=1.5, credits_used_compute=1.4,
             credits_used_cloud_services=0.1),
        dict(start_time=_ts(10), end_time=_ts(11), warehouse_name="ETL_WH",
             credits_used=0.5, credits_used_compute=0.5,
             credits_used_cloud_services=0.0),
        dict(start_time=_ts(9, day=11), end_time=_ts(10, day=11),
             warehouse_name="COMPUTE_WH", credits_used=0.25,
             credits_used_compute=0.2, credits_used_cloud_services=0.05),
    ]),
    "warehouse_load_history": _t([
        dict(hour_start=_ts(9), warehouse_name="ETL_WH", avg_running=2.5,
             avg_queued_load=0.2, avg_queued_provisioning=0.0, avg_blocked=0.0,
             peak_avg_running=4.0, n_intervals=12),
        dict(hour_start=_ts(9, day=11), warehouse_name="COMPUTE_WH",
             avg_running=0.5, avg_queued_load=0.0, avg_queued_provisioning=0.0,
             avg_blocked=0.0, peak_avg_running=1.0, n_intervals=12),
    ]),
    "metering_daily_history": _t([
        dict(usage_date=date(2026, 8, 10), service_type="WAREHOUSE_METERING",
             credits_used_compute=10.0, credits_used_cloud_services=1.0,
             credits_used=11.0, credits_adjustment_cloud_services=-0.2,
             credits_billed=10.8),
        dict(usage_date=date(2026, 8, 10), service_type="PIPE",
             credits_used_compute=0.3, credits_used_cloud_services=0.0,
             credits_used=0.3, credits_adjustment_cloud_services=0.0,
             credits_billed=0.3),
    ]),
    "copy_history": _t([
        dict(table_catalog="APPDB", table_schema="S1", table_name="PLAIN",
             load_date=date(2026, 8, 10), load_method="snowpipe",
             load_status="loaded", n_files=24, rows_loaded=24000,
             bytes_loaded=48000, n_files_with_errors=0,
             first_load_time=_ts(0), last_load_time=_ts(23)),
        dict(table_catalog="APPDB", table_schema="S1", table_name="PLAIN",
             load_date=date(2026, 8, 11), load_method="snowpipe",
             load_status="loaded", n_files=24, rows_loaded=24000,
             bytes_loaded=48000, n_files_with_errors=1,
             first_load_time=_ts(0, day=11), last_load_time=_ts(23, day=11)),
        # same table+day also had failures: evidence, never writes
        dict(table_catalog="APPDB", table_schema="S1", table_name="PLAIN",
             load_date=date(2026, 8, 11), load_method="snowpipe",
             load_status="failed", n_files=3, rows_loaded=0,
             bytes_loaded=0, n_files_with_errors=3,
             first_load_time=_ts(1, day=11), last_load_time=_ts(4, day=11)),
        dict(table_catalog="APPDB", table_schema="S1", table_name="ORDERS",
             load_date=date(2026, 8, 11), load_method="copy_into",
             load_status="loaded", n_files=1, rows_loaded=500,
             bytes_loaded=9000, n_files_with_errors=0,
             first_load_time=_ts(2, day=11), last_load_time=_ts(2, day=11)),
        # a table whose only load attempts failed must not appear as written
        dict(table_catalog="APPDB", table_schema="S1", table_name="BROKEN_T",
             load_date=date(2026, 8, 11), load_method="copy_into",
             load_status="failed", n_files=2, rows_loaded=0,
             bytes_loaded=0, n_files_with_errors=2,
             first_load_time=_ts(5, day=11), last_load_time=_ts(6, day=11)),
    ]),
    "pipe_usage_history": _t([
        dict(pipe_id=101, pipe_database="APPDB", pipe_schema="S1",
             pipe_name="APPDB.S1.LOAD_EVENTS", source_kind="snowpipe",
             usage_date=date(2026, 8, 10), credits_used=0.3,
             bytes_inserted=48000, files_inserted=24, n_intervals=10),
        # a named row that missed the PIPES join (Iceberg refresh, aged-out
        # pipe, or role-hidden pipe): present, never 'snowpipe'
        dict(pipe_id=202, pipe_database="APPDB", pipe_schema="S1",
             pipe_name="APPDB.S1.ICEBERG_T", source_kind="unclassified",
             usage_date=date(2026, 8, 10), credits_used=0.1,
             bytes_inserted=1000, files_inserted=2, n_intervals=4),
    ]),
    "task_history": _t([
        dict(task_database="APPDB", task_schema="S1",
             task_name="NIGHTLY_ROLLUP", run_date=date(2026, 8, 10), n_runs=1,
             n_succeeded=1, n_failed=0, first_scheduled_time=_ts(2),
             last_scheduled_time=_ts(2)),
    ]),
    "login_history": _t([
        dict(user_name="ETL_SVC", client_type="PYTHON_DRIVER",
             client_version="3.12.0", n_logins=48, n_successful=48,
             first_seen=_ts(0), last_seen=_ts(23, day=11)),
        dict(user_name="JANE", client_type="SNOWFLAKE_UI", client_version=None,
             n_logins=5, n_successful=5, first_seen=_ts(8), last_seen=_ts(18)),
    ]),
}


REALISTIC_SHOW = {
    "streams": _t([
        dict(name="ORDERS_STREAM", database_name="APPDB", schema_name="S1",
             owner="OWNER", table_name="ORDERS", source_type="table",
             base_tables="APPDB.S1.ORDERS", type="DELTA", stale="false",
             mode="DEFAULT", comment=None),
    ]),
    "warehouses": _t([
        dict(name="COMPUTE_WH", state="SUSPENDED", type="STANDARD", size="X-Small",
             min_cluster_count=1, max_cluster_count=1, auto_suspend=600,
             auto_resume="true", scaling_policy="STANDARD", owner="OWNER",
             comment=None),
        dict(name="ETL_WH", state="STARTED", type="STANDARD", size="Medium",
             min_cluster_count=1, max_cluster_count=3, auto_suspend=60,
             auto_resume="true", scaling_policy="ECONOMY", owner="OWNER",
             comment=None),
        dict(name="SYSTEM$STREAMLIT_NOTEBOOK_WH", state="SUSPENDED",
             type="STANDARD", size="X-Small", min_cluster_count=1,
             max_cluster_count=1, auto_suspend=60, auto_resume="true",
             scaling_policy="STANDARD", owner=None, comment=None),
    ]),
    "streamlit_apps": _t([
        dict(name="SALES_DASH", database_name="APPDB", schema_name="S1",
             title="Sales dashboard", owner="OWNER", query_warehouse="COMPUTE_WH",
             comment=None),
    ]),
    "notebooks": _t([
        dict(name="EDA_NOTEBOOK", database_name="APPDB", schema_name="S1",
             owner="OWNER", query_warehouse="COMPUTE_WH", comment=None),
    ]),
    "applications": _t([
        dict(name="SNOWFLAKE", source_type="", source="", owner=None,
             version=None, comment=None),
        dict(name="PARTNER_APP", source_type="listing", source="PARTNER.LST",
             owner="OWNER", version="1.0", comment=None),
    ]),
    "application_packages": _t([
        dict(name="MY_PKG", distribution="INTERNAL", owner="OWNER", comment=None),
    ]),
    "catalog_integrations": _t([
        dict(name="GLUE_CAT", type="CATALOG", category="GLUE", enabled="true",
             comment=None),
    ]),
    "show_shares": _t([
        dict(kind="INBOUND", owner_account="SNOWFLAKE", name="SNOWFLAKE.ACCOUNT_USAGE",
             database_name="SNOWFLAKE", owner=None, listing_global_name=None,
             secure_objects_only="true", comment=None),
        dict(kind="INBOUND", owner_account="SFSALES", name="SFSALES.SAMPLES",
             database_name="SNOWFLAKE_SAMPLE_DATA", owner=None,
             listing_global_name=None, secure_objects_only="true", comment=None),
        dict(kind="INBOUND", owner_account="PARTNERORG", name="PARTNERORG.SHARE1",
             database_name="PARTNER_DATA", owner=None, listing_global_name=None,
             secure_objects_only="true", comment=None),
        dict(kind="OUTBOUND", owner_account="SELF", name="MDA_TEST_SHARE",
             database_name="APPDB", owner="ACCOUNTADMIN", listing_global_name=None,
             secure_objects_only="false", comment=None),
    ]),
}
