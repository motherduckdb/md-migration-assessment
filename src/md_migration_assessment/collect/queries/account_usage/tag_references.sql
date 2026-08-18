-- Tag assignments to objects/columns. Column list verified live 2026-08-17.
SELECT
    tag_database,
    tag_schema,
    tag_name,
    tag_value,
    object_database,
    object_schema,
    object_name,
    domain,
    column_name,
    apply_method
FROM snowflake.account_usage.tag_references
WHERE object_deleted IS NULL
{scope_filter}
