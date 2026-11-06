"""Databricks feature signals: factual counts over collected evidence.

Each probe counts objects using a Databricks feature that matters when
moving to MotherDuck (open-format tables readable in place, Delta table
features, Unity Catalog governance objects, platform objects with no
direct equivalent). No compatibility ratings; the report layer turns an
unavailable source extract into UNKNOWN, never zero.
"""

from __future__ import annotations

from ...report.signals import PlannedSignal, Signal
from ...report.signals import probe as _neutral_probe

_SYSTEM_CATALOGS = "('system', 'samples', 'hive_metastore', '__databricks_internal')"


def _not_system(cat_col: str, name_col: str | None = None) -> str:
    """Drop platform catalogs and the hidden tables MVs/streaming tables create."""
    cond = f"{cat_col} NOT IN {_SYSTEM_CATALOGS}"
    if name_col:
        cond += (
            f" AND {name_col} NOT LIKE '\\_\\_materialization\\_%' ESCAPE '\\'"
            f" AND {name_col} NOT LIKE 'event\\_log\\_%' ESCAPE '\\'"
        )
    return cond


def _tbl(name: str, category: str, source: str, predicate: str) -> Signal:
    return _neutral_probe(
        name, category, source, predicate, _TBL,
        exclude=_not_system("table_catalog", "table_name"),
    )


def _plain(name: str, category: str, source: str, predicate: str, obj: str) -> Signal:
    return _neutral_probe(name, category, source, predicate, obj)


_TBL = "table_catalog || '.' || table_schema || '.' || table_name"
_COL = _TBL + " || '.' || column_name"
_FEATURE = "coalesce(table_features, '')"
_JOB = ("coalesce(job_name, job_id) || ': ' || coalesce(task_type, '?')"
        " || coalesce(' (' || file_language || ')', '')")

SIGNALS: list[Signal] = [
    # ── table layout / format ──────────────────────────────────────────
    _tbl("managed_iceberg_tables", "table_layout", "tables",
         "upper(coalesce(data_source_format, '')) = 'ICEBERG'"),
    _tbl("uniform_iceberg_tables", "table_layout", "table_details",
         "lower(coalesce(uniform_formats, '')) LIKE '%iceberg%'"),
    _tbl("external_tables", "table_layout", "tables", "table_type = 'EXTERNAL'"),
    _tbl("non_delta_formats", "table_layout", "tables",
         "table_type IN ('MANAGED', 'EXTERNAL') AND upper(coalesce(data_source_format, '')) "
         "NOT IN ('DELTA', 'ICEBERG')"),
    _tbl("shallow_clones", "table_layout", "tables", "table_type LIKE '%SHALLOW_CLONE'"),
    _tbl("materialized_views", "table_layout", "tables", "table_type = 'MATERIALIZED_VIEW'"),
    _tbl("streaming_tables", "table_layout", "tables", "table_type = 'STREAMING_TABLE'"),
    _tbl("foreign_tables", "platform", "tables", "table_type = 'FOREIGN'"),
    _tbl("partitioned_tables", "table_layout", "table_details",
         "coalesce(partition_columns, '') <> ''"),
    _tbl("liquid_clustering", "table_layout", "table_details",
         "coalesce(clustering_columns, '') <> ''"),
    _tbl("deletion_vectors", "table_layout", "table_details",
         f"dv_enabled OR {_FEATURE} LIKE '%deletionVectors%'"),
    _tbl("change_data_feed", "table_layout", "table_details",
         f"cdf_enabled OR {_FEATURE} LIKE '%changeDataFeed%'"),
    _tbl("column_mapping", "table_layout", "table_details",
         "lower(coalesce(column_mapping_mode, 'none')) <> 'none'"),
    _tbl("table_detail_errors", "platform", "table_details", "detail_error IS NOT NULL"),
    # ── types ──────────────────────────────────────────────────────────
    # information_schema.columns reports is_identity/is_generated = 'NO'
    # even when they are set (found live), so these read Delta table features.
    _tbl("identity_columns", "types", "table_details", f"{_FEATURE} LIKE '%identityColumns%'"),
    _tbl("generated_columns", "types", "table_details", f"{_FEATURE} LIKE '%generatedColumns%'"),
    _neutral_probe("variant_columns", "types", "columns",
                   "upper(data_type) = 'VARIANT'", _COL,
                   exclude=_not_system("table_catalog", "table_name")),
    _neutral_probe("complex_type_columns", "types", "columns",
                   "upper(data_type) IN ('STRUCT', 'ARRAY', 'MAP')", _COL,
                   exclude=_not_system("table_catalog", "table_name")),
    # ── code ───────────────────────────────────────────────────────────
    _neutral_probe("sql_udfs", "code", "routines",
                   "upper(coalesce(routine_body, '')) = 'SQL'",
                   "routine_catalog || '.' || routine_schema || '.' || routine_name",
                   exclude=_not_system("routine_catalog")),
    _neutral_probe("python_udfs", "code", "routines",
                   "lower(coalesce(external_language, '')) = 'python'",
                   "routine_catalog || '.' || routine_schema || '.' || routine_name",
                   exclude=_not_system("routine_catalog")),
    # ── security / governance ──────────────────────────────────────────
    _plain("column_masks", "security", "column_masks", "true", _COL),
    _plain("row_filters", "security", "row_filters", "true", _TBL),
    _plain("table_tags", "security", "table_tags", "true",
           "catalog_name || '.' || schema_name || '.' || table_name || ':' || tag_name"),
    _plain("column_tags", "security", "column_tags", "true",
           "catalog_name || '.' || schema_name || '.' || table_name || '.' || column_name "
           "|| ':' || tag_name"),
    _tbl("informational_constraints", "security", "table_constraints",
         "constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')"),
    # ── platform objects ───────────────────────────────────────────────
    _plain("volumes", "platform", "volumes", "true",
           "volume_catalog || '.' || volume_schema || '.' || volume_name"),
    _plain("external_locations", "platform", "external_locations", "true",
           "external_location_name"),
    _plain("federation_connections", "platform", "connections", "true",
           "connection_name || ' (' || coalesce(connection_type, '?') || ')'"),
    _plain("delta_shares", "platform", "shares", "true", "share_name"),
    _plain("hive_metastore_tables", "platform", "hive_metastore_tables",
           "table_name IS NOT NULL",
           "table_catalog || '.' || table_schema || '.' || table_name"),
    # ── compute, jobs, pipelines, serving (system tables) ──────────────
    _plain("sql_warehouses", "platform", "warehouses", "delete_time IS NULL",
           "warehouse_name || ' (' || coalesce(warehouse_type, '?') || ' '"
           " || coalesce(warehouse_size, '?') || ')'"),
    _plain("classic_clusters", "platform", "clusters",
           "delete_time IS NULL AND coalesce(cluster_source, '') NOT IN ('PIPELINE', 'PIPELINE_MAINTENANCE')",
           "cluster_name || ' (' || coalesce(cluster_source, '?') || ')'"),
    _plain("lakeflow_jobs", "platform", "jobs", "delete_time IS NULL", "job_name"),
    _plain("scheduled_jobs", "platform", "jobs",
           "delete_time IS NULL AND coalesce(trigger_type, '') NOT IN ('', 'MANUAL') "
           "AND NOT coalesce(paused, false)",
           "job_name || ' (' || trigger_type || ')'"),
    _plain("lakeflow_pipelines", "platform", "pipelines", "delete_time IS NULL",
           "pipeline_name || ' (' || coalesce(pipeline_type, '?') || ')'"),
    _plain("continuous_pipelines", "platform", "pipelines",
           "delete_time IS NULL AND coalesce(continuous, false)", "pipeline_name"),
    _plain("model_serving_endpoints", "platform", "served_entities",
           "endpoint_delete_time IS NULL", "endpoint_name"),
    _plain("pipeline_or_job_written_tables", "platform", "table_lineage_rollup",
           "target_table_full_name IS NOT NULL AND entity_type IN ('JOB', 'PIPELINE') "
           "AND split_part(target_table_full_name, '.', 1) NOT IN " + _SYSTEM_CATALOGS,
           "target_table_full_name || ' <- ' || entity_type"),
    _plain("table_lineage_edges", "platform", "table_lineage_rollup",
           "source_table_full_name IS NOT NULL AND target_table_full_name IS NOT NULL "
           "AND split_part(target_table_full_name, '.', 1) NOT IN " + _SYSTEM_CATALOGS,
           "source_table_full_name || ' -> ' || target_table_full_name"),
    # ── job code (Jobs API task definitions; no code is read) ──────────
    _plain("notebook_job_tasks", "code", "job_tasks", "task_type = 'notebook'", _JOB),
    _plain("python_job_tasks", "code", "job_tasks", "file_language = 'python'", _JOB),
    _plain("scala_or_jvm_job_tasks", "code", "job_tasks",
           "file_language IN ('scala', 'jvm') OR task_type = 'spark_submit'", _JOB),
    _plain("sql_or_dbt_job_tasks", "code", "job_tasks",
           "task_type IN ('sql', 'dbt', 'dbt_cloud', 'dbt_platform') "
           "OR (task_type = 'notebook' AND file_language = 'sql')", _JOB),
    _plain("r_job_tasks", "code", "job_tasks", "file_language = 'r'", _JOB),
    _plain("all_purpose_cluster_job_tasks", "platform", "job_tasks",
           "compute_kind = 'all_purpose_cluster'", _JOB),
    _plain("job_task_libraries", "code", "job_tasks", "library_kinds IS NOT NULL",
           "coalesce(job_name, job_id) || ' (' || library_kinds || ')'"),
    _plain("event_triggered_jobs", "platform", "job_tasks",
           "task_index = '0' AND trigger_kind IN ('file_arrival', 'table_update', 'continuous') "
           "AND NOT coalesce(trigger_paused, false)",
           "coalesce(job_name, job_id) || ' (' || trigger_kind || ')'"),
    Signal(
        name="dbu_usage", category="platform", source_extractor="billing_usage_daily",
        sql=(
            "SELECT coalesce(round(sum(usage_quantity)), 0)::BIGINT AS n,\n"
            "       coalesce(list(DISTINCT billing_origin_product ORDER BY billing_origin_product), [])\n"
            "           AS sample_objects\n"
            "FROM raw.billing_usage_daily WHERE collection_id = '{cid}'"
        ),
    ),
    Signal(
        name="workspace_services_used", category="platform",
        source_extractor="workspace_feature_usage",
        sql=(
            "SELECT count(DISTINCT service_name)::BIGINT AS n,\n"
            "       coalesce(list(DISTINCT service_name ORDER BY service_name), [])\n"
            "           AS sample_objects\n"
            "FROM raw.workspace_feature_usage WHERE collection_id = '{cid}'"
        ),
    ),
    Signal(
        name="predictive_optimization_operations", category="table_layout",
        source_extractor="predictive_optimization_rollup",
        sql=(
            "SELECT coalesce(sum(n_operations), 0)::BIGINT AS n,\n"
            "       coalesce(list(DISTINCT operation_type ORDER BY operation_type), [])\n"
            "           AS sample_objects\n"
            "FROM raw.predictive_optimization_rollup WHERE collection_id = '{cid}'"
        ),
    ),
    # ── workload ───────────────────────────────────────────────────────
    # statements in the history window; samples are the statement types
    Signal(
        name="query_workload", category="platform",
        source_extractor="query_workload_rollup",
        sql=(
            "SELECT coalesce(sum(n_queries), 0)::BIGINT AS n,\n"
            "       coalesce(list(DISTINCT statement_type ORDER BY statement_type), [])\n"
            "           AS sample_objects\n"
            "FROM raw.query_workload_rollup WHERE collection_id = '{cid}'"
        ),
    ),
]

PLANNED_SIGNALS: list[PlannedSignal] = [
    PlannedSignal("delta_sharing_recipients", "platform",
                  "share recipients need SHOW RECIPIENTS (metastore admin); not collected"),
]
