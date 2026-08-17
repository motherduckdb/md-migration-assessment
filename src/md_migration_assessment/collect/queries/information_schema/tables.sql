SELECT
    table_catalog,
    table_schema,
    table_name,
    table_owner,
    table_type,
    is_transient,
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
