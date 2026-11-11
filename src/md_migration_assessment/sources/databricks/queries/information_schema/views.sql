-- View definitions in each catalog and whether each is materialized.
-- The SQL text stays in the local file; publish drops it.
SELECT table_catalog, table_schema, table_name, is_materialized, view_definition
FROM {database}.information_schema.views
WHERE table_schema <> 'information_schema'
{scope_filter}
