-- Column selection informed by google/dwh-migration-tools (Apache-2.0),
-- dumper snowflake connector.
SELECT
    function_catalog,
    function_schema,
    function_name,
    function_owner,
    function_language,
    argument_signature,
    data_type,
    function_definition,
    is_external,
    is_secure,
    volatility,
    packages,
    runtime_version,
    created,
    last_altered,
    comment
FROM {database}.information_schema.functions
WHERE function_schema <> 'INFORMATION_SCHEMA'
{scope_filter}