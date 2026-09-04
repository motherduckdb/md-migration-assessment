-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
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
FROM snowflake.account_usage.schemata
WHERE deleted IS NULL
{scope_filter}
