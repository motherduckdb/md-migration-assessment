-- Declared constraints per table (such as primary and foreign keys)
-- and whether each is enforced.
SELECT table_catalog, table_schema, table_name, constraint_name, constraint_type, enforced
FROM {database}.information_schema.table_constraints
WHERE table_schema <> 'information_schema'
{scope_filter}
