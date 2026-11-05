SELECT table_catalog, table_schema, table_name, is_materialized, view_definition
FROM {database}.information_schema.views
WHERE table_schema <> 'information_schema'
{scope_filter}
