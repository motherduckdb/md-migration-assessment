-- Declared PK/UNIQUE constraint inventory (M3d) — lite-capable fallback.
SELECT
    table_catalog,
    table_schema,
    table_name,
    constraint_name,
    constraint_type
FROM {database}.information_schema.table_constraints
WHERE table_schema <> 'INFORMATION_SCHEMA'
{scope_filter}
