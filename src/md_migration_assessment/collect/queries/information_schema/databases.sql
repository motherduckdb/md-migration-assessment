-- Column selection informed by google/dwh-migration-tools (Apache-2.0),
-- dumper snowflake connector.
-- Fallback: run once per accessible database; the predicate keeps each walk
-- from re-listing every visible database.
SELECT
    database_name,
    database_owner,
    is_transient,
    retention_time,
    created,
    last_altered,
    comment
FROM {database}.information_schema.databases
WHERE database_name = '{database_literal}'
{scope_filter}