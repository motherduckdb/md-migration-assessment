-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
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
FROM snowflake.account_usage.tables
WHERE deleted IS NULL
{scope_filter}
