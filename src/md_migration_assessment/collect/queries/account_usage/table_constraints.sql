-- Declared PK/UNIQUE constraint inventory (M3d, decision 18). Snowflake does
-- not enforce these; what they mean on MotherDuck is overlay judgment. The
-- inventory feeds dbt-test porting and model design.
SELECT
    table_catalog,
    table_schema,
    table_name,
    constraint_name,
    constraint_type
FROM snowflake.account_usage.table_constraints
WHERE deleted IS NULL
{scope_filter}
