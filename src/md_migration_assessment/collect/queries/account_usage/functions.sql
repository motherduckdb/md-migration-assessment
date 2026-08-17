-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
-- function_definition is classified source_body (see views.sql note).
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
    -- IS_SECURE exists in INFORMATION_SCHEMA.FUNCTIONS but not in
    -- ACCOUNT_USAGE.FUNCTIONS (verified live 2026-08-17); NULL-padded to keep
    -- both sources producing the same raw shape.
    CAST(NULL AS VARCHAR) AS is_secure,
    volatility,
    created,
    last_altered,
    comment
FROM snowflake.account_usage.functions
WHERE deleted IS NULL
{scope_filter}
