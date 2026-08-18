-- External tables have no ACCOUNT_USAGE view; collected via the per-database
-- INFORMATION_SCHEMA walk. Column list verified live 2026-08-18.
SELECT
    table_catalog,
    table_schema,
    table_name,
    table_owner,
    location,
    file_format_name,
    file_format_type,
    created,
    last_altered,
    comment
FROM {database}.information_schema.external_tables
WHERE table_schema <> 'INFORMATION_SCHEMA'
{scope_filter}
