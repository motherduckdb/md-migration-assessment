SELECT
    catalog_name,
    schema_name,
    schema_owner,
    is_transient,
    is_managed_access,
    retention_time,
    created,
    last_altered,
    comment
FROM {database}.information_schema.schemata
WHERE schema_name <> 'INFORMATION_SCHEMA'
{scope_filter}
