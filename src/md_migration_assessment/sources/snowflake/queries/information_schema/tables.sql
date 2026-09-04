-- Column selection informed by google/dwh-migration-tools (Apache-2.0),
-- dumper snowflake connector.
SELECT
    table_catalog,
    table_schema,
    table_name,
    table_owner,
    table_type,
    is_transient,
    is_iceberg,
    is_dynamic,
    is_hybrid,
    clustering_key,
    auto_clustering_on,
    row_count,
    bytes,
    retention_time,
    created,
    last_altered,
    last_ddl,
    comment
FROM {database}.information_schema.tables
WHERE table_schema <> 'INFORMATION_SCHEMA'
{scope_filter}