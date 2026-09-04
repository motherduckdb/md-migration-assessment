-- Declared FK graph (M3d, decision 18). Not enforced by Snowflake either;
-- informs dbt relationship tests and model design.
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
FROM snowflake.account_usage.referential_constraints
WHERE deleted IS NULL
{scope_filter}
