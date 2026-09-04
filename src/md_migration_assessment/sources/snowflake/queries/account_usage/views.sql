-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
-- view_definition is classified source_body: retained locally for dialect
-- assessment, excluded from the default handoff database.
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
FROM snowflake.account_usage.views
WHERE deleted IS NULL
{scope_filter}
