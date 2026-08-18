-- Which objects/columns have policies attached (masking, row access,
-- aggregation, projection). Column list verified live 2026-08-17.
SELECT
    policy_db,
    policy_schema,
    policy_name,
    policy_kind,
    policy_status,
    ref_database_name,
    ref_schema_name,
    ref_entity_name,
    ref_entity_domain,
    ref_column_name,
    tag_database,
    tag_schema,
    tag_name
FROM snowflake.account_usage.policy_references
WHERE true
{scope_filter}
