"""Feature-signal definitions: the public, factual half of the taxonomy.

Each signal is a SQL probe over raw evidence returning an observed count and a
sample of affected objects. Signals carry NO MotherDuck judgment — ratings and
effort live in the internal overlay repo (spec decision 14). The signal list is
ported from google/dwh-migration-tools' snowflake-features scripts
(Apache-2.0), adapted to run over collected evidence in DuckDB rather than
live against Snowflake.

Every signal names its source extractor so the report builder can propagate
observation status from meta.extract_runs: a signal over an extract that was
unavailable is UNKNOWN, never zero.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Predicate excluding Snowflake's own furniture from feature counts.
def _not_system(cat_col: str) -> str:
    return f"{cat_col} <> 'SNOWFLAKE' AND {cat_col} NOT LIKE 'USER$%'"


@dataclass(frozen=True)
class Signal:
    name: str
    category: str  # table_layout | types | code | security | platform
    source_extractor: str
    #: SQL with a {cid} placeholder; must return (n BIGINT, sample_objects VARCHAR[]).
    sql: str


def _probe(
    name: str,
    category: str,
    source: str,
    predicate: str,
    obj_expr: str,
    *,
    catalog_col: str | None = None,
    table: str | None = None,
) -> Signal:
    table = table or source
    conds = [f"collection_id = '{{cid}}'", f"({predicate})"]
    if catalog_col:
        conds.append(_not_system(catalog_col))
    where = " AND ".join(conds)
    sql = (
        f'WITH hits AS (SELECT {obj_expr} AS obj FROM raw."{table}" WHERE {where})\n'
        "SELECT (SELECT count(*) FROM hits) AS n,\n"
        "       (SELECT coalesce(list(obj), []) FROM\n"
        "          (SELECT DISTINCT obj FROM hits ORDER BY obj LIMIT 20)) AS sample_objects"
    )
    return Signal(name=name, category=category, source_extractor=source, sql=sql)


_TBL = "table_catalog || '.' || table_schema || '.' || table_name"
_COL = _TBL + " || '.' || column_name"
_FN = "function_catalog || '.' || function_schema || '.' || function_name"
_PROC = "procedure_catalog || '.' || procedure_schema || '.' || procedure_name"
_YES = "IN ('YES', 'TRUE', 'Y')"

# Clustering keys look like LINEAR(COL_A, COL_B) for plain columns; anything
# with nested parens inside the LINEAR(...) wrapper is expression-based.
_CK_INNER = (
    "CASE WHEN starts_with(upper(clustering_key), 'LINEAR(') "
    "THEN substr(clustering_key, 8, length(clustering_key) - 8) "
    "ELSE clustering_key END"
)

SIGNALS: list[Signal] = [
    # ── table layout ────────────────────────────────────────────────
    _probe("transient_tables", "table_layout", "tables",
           f"table_type = 'BASE TABLE' AND upper(coalesce(is_transient::VARCHAR, 'NO')) {_YES}",
           _TBL, catalog_col="table_catalog"),
    _probe("iceberg_tables", "table_layout", "tables",
           f"upper(coalesce(is_iceberg::VARCHAR, 'NO')) {_YES}", _TBL, catalog_col="table_catalog"),
    _probe("dynamic_tables", "table_layout", "tables",
           f"upper(coalesce(is_dynamic::VARCHAR, 'NO')) {_YES}", _TBL, catalog_col="table_catalog"),
    _probe("hybrid_tables", "table_layout", "tables",
           f"upper(coalesce(is_hybrid::VARCHAR, 'NO')) {_YES}", _TBL, catalog_col="table_catalog"),
    _probe("materialized_views", "table_layout", "tables",
           "table_type = 'MATERIALIZED VIEW'", _TBL, catalog_col="table_catalog"),
    _probe("clustered_tables_by_column", "table_layout", "tables",
           f"clustering_key IS NOT NULL AND NOT contains({_CK_INNER}, '(')",
           _TBL, catalog_col="table_catalog"),
    _probe("clustered_tables_by_expression", "table_layout", "tables",
           f"clustering_key IS NOT NULL AND contains({_CK_INNER}, '(')",
           _TBL, catalog_col="table_catalog"),
    _probe("auto_clustering_enabled", "table_layout", "tables",
           f"upper(coalesce(auto_clustering_on::VARCHAR, 'NO')) {_YES}",
           _TBL, catalog_col="table_catalog"),
    _probe("extended_time_travel_8_14d", "table_layout", "tables",
           "coalesce(retention_time, 1) BETWEEN 8 AND 14", _TBL, catalog_col="table_catalog"),
    _probe("extended_time_travel_gt_14d", "table_layout", "tables",
           "coalesce(retention_time, 1) > 14", _TBL, catalog_col="table_catalog"),
    _probe("zero_copy_clones", "table_layout", "table_storage_metrics",
           "clone_group_id IS NOT NULL AND clone_group_id <> id AND table_catalog IS NOT NULL",
           _TBL, catalog_col="table_catalog"),
    # ── data types ──────────────────────────────────────────────────
    _probe("variant_columns", "types", "columns",
           "data_type = 'VARIANT'", _COL, catalog_col="table_catalog"),
    _probe("object_columns", "types", "columns",
           "data_type = 'OBJECT'", _COL, catalog_col="table_catalog"),
    _probe("array_columns", "types", "columns",
           "data_type = 'ARRAY'", _COL, catalog_col="table_catalog"),
    _probe("geospatial_columns", "types", "columns",
           "data_type IN ('GEOGRAPHY', 'GEOMETRY')", _COL, catalog_col="table_catalog"),
    _probe("vector_columns", "types", "columns",
           "data_type LIKE 'VECTOR%'", _COL, catalog_col="table_catalog"),
    _probe("timestamp_tz_columns", "types", "columns",
           "data_type = 'TIMESTAMP_TZ'", _COL, catalog_col="table_catalog"),
    _probe("timestamp_ltz_columns", "types", "columns",
           "data_type = 'TIMESTAMP_LTZ'", _COL, catalog_col="table_catalog"),
    _probe("nanosecond_timestamp_columns", "types", "columns",
           "data_type LIKE 'TIMESTAMP%' AND datetime_precision = 9",
           _COL, catalog_col="table_catalog"),
    # ── code ────────────────────────────────────────────────────────
    _probe("javascript_udfs", "code", "functions",
           "upper(coalesce(function_language, '')) = 'JAVASCRIPT'",
           _FN, catalog_col="function_catalog"),
    _probe("python_udfs", "code", "functions",
           "upper(coalesce(function_language, '')) = 'PYTHON'",
           _FN, catalog_col="function_catalog"),
    _probe("java_scala_udfs", "code", "functions",
           "upper(coalesce(function_language, '')) IN ('JAVA', 'SCALA')",
           _FN, catalog_col="function_catalog"),
    _probe("external_functions", "code", "functions",
           f"upper(coalesce(is_external::VARCHAR, 'NO')) {_YES}", _FN, catalog_col="function_catalog"),
    _probe("snowpark_udfs", "code", "functions",
           "packages IS NOT NULL AND lower(packages) LIKE '%snowpark%'",
           _FN, catalog_col="function_catalog"),
    _probe("stored_procedures", "code", "procedures",
           "true", _PROC, catalog_col="procedure_catalog"),
    _probe("javascript_procedures", "code", "procedures",
           "upper(coalesce(procedure_language, '')) = 'JAVASCRIPT'",
           _PROC, catalog_col="procedure_catalog"),
    _probe("python_procedures", "code", "procedures",
           "upper(coalesce(procedure_language, '')) = 'PYTHON'",
           _PROC, catalog_col="procedure_catalog"),
    # ── security / governance ───────────────────────────────────────
    _probe("secure_views", "security", "views",
           f"upper(coalesce(is_secure::VARCHAR, 'NO')) {_YES}", _TBL, catalog_col="table_catalog"),
    _probe("masking_policies", "security", "masking_policies",
           "true", "policy_catalog || '.' || policy_schema || '.' || policy_name",
           catalog_col="policy_catalog"),
    _probe("row_access_policies", "security", "row_access_policies",
           "true", "policy_catalog || '.' || policy_schema || '.' || policy_name",
           catalog_col="policy_catalog"),
    _probe("masking_protected_columns", "security", "policy_references",
           "policy_kind = 'MASKING_POLICY' AND ref_column_name IS NOT NULL",
           "ref_database_name || '.' || ref_schema_name || '.' || ref_entity_name"
           " || '.' || ref_column_name",
           catalog_col="ref_database_name"),
    _probe("row_access_protected_objects", "security", "policy_references",
           "policy_kind = 'ROW_ACCESS_POLICY'",
           "ref_database_name || '.' || ref_schema_name || '.' || ref_entity_name",
           catalog_col="ref_database_name"),
    _probe("tags", "security", "tags",
           "true", "tag_database || '.' || tag_schema || '.' || tag_name",
           catalog_col="tag_database"),
    _probe("tag_assignments", "security", "tag_references",
           "true",
           "object_database || '.' || object_schema || '.' || object_name",
           catalog_col="object_database"),
    _probe("custom_roles", "security", "roles",
           "coalesce(role_type, 'ROLE') = 'ROLE' AND name NOT IN "
           "('ACCOUNTADMIN', 'ORGADMIN', 'GLOBALORGADMIN', 'SECURITYADMIN', "
           "'SYSADMIN', 'USERADMIN', 'PUBLIC')",
           "name"),
    # ── platform ────────────────────────────────────────────────────
    _probe("snowpipes", "platform", "pipes",
           "true", "pipe_catalog || '.' || pipe_schema || '.' || pipe_name",
           catalog_col="pipe_catalog"),
    _probe("auto_ingest_pipes", "platform", "pipes",
           f"upper(coalesce(is_autoingest_enabled::VARCHAR, 'NO')) {_YES}",
           "pipe_catalog || '.' || pipe_schema || '.' || pipe_name",
           catalog_col="pipe_catalog"),
]
