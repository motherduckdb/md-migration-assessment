-- Stage inventory (data-loading surface). Column list verified live against
-- ACCOUNT_USAGE 2026-08-17.
SELECT
    stage_catalog,
    stage_schema,
    stage_name,
    stage_owner,
    stage_url,
    stage_region,
    stage_type,
    storage_integration,
    directory_enabled,
    created,
    last_altered,
    comment
FROM snowflake.account_usage.stages
WHERE deleted IS NULL
{scope_filter}
