-- Tags set on tables, with name and value, in each catalog.
SELECT catalog_name, schema_name, table_name, tag_name, tag_value
FROM {database}.information_schema.table_tags
WHERE true
{scope_filter}
