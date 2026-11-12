-- Tags set on columns, with name and value, in each catalog.
SELECT catalog_name, schema_name, table_name, column_name, tag_name, tag_value
FROM {database}.information_schema.column_tags
WHERE true
{scope_filter}
