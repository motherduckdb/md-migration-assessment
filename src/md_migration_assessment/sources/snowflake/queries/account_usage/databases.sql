-- Column selection informed by google/dwh-migration-tools (Apache-2.0),
-- dumper snowflake connector.
SELECT
    database_name,
    database_owner,
    is_transient,
    retention_time,
    created,
    last_altered,
    comment
FROM snowflake.account_usage.databases
WHERE deleted IS NULL
{scope_filter}
