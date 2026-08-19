-- Column selection informed by google/dwh-migration-tools (Apache-2.0),
-- dumper snowflake connector.
-- view_definition is NULL here unless the role owns the view; ACCOUNT_USAGE
-- is the authoritative source when available.
SELECT
    table_catalog,
    table_schema,
    table_name,
    table_owner,
    view_definition,
    is_secure,
    created,
    last_altered,
    comment
FROM {database}.information_schema.views
WHERE table_schema <> 'INFORMATION_SCHEMA'
{scope_filter}