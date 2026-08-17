-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
SELECT
    table_catalog,
    table_schema,
    table_name,
    column_name,
    ordinal_position,
    column_default,
    is_nullable,
    data_type,
    character_maximum_length,
    numeric_precision,
    numeric_scale,
    datetime_precision,
    is_identity,
    comment
FROM snowflake.account_usage.columns
WHERE deleted IS NULL
{scope_filter}
