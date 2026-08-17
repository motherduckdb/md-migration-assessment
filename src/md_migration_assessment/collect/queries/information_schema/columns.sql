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
FROM {database}.information_schema.columns
WHERE table_schema <> 'INFORMATION_SCHEMA'
{scope_filter}
