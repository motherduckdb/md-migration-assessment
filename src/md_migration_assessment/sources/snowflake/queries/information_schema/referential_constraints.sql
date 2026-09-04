-- Declared FK graph (M3d) — lite-capable fallback.
SELECT
    constraint_catalog,
    constraint_schema,
    constraint_name,
    unique_constraint_catalog,
    unique_constraint_schema,
    unique_constraint_name,
    match_option,
    update_rule,
    delete_rule
FROM {database}.information_schema.referential_constraints
WHERE constraint_schema <> 'INFORMATION_SCHEMA'
{scope_filter}
