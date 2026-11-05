SELECT table_catalog, table_schema, table_name, filter_name, target_columns
FROM {database}.information_schema.row_filters
WHERE true
{scope_filter}
