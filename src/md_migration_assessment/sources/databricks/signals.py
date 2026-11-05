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
                  "share recipients need SHOW RECIPIENTS (metastore admin); not collected in v1"),
    PlannedSignal("jobs_and_pipelines", "platform",
                  "system.lakeflow jobs/pipelines not collected in v1"),
    PlannedSignal("classic_cluster_workloads", "platform",
                  "system.compute clusters and Spark workloads not collected in v1"),
    PlannedSignal("spend_profile", "platform",
                  "system.billing usage/list prices not collected in v1"),
    PlannedSignal("table_lineage", "platform",
                  "system.access.table_lineage not collected in v1"),
]
