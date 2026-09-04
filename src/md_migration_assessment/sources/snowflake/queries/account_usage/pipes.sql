-- Snowpipe inventory. Column list verified live against ACCOUNT_USAGE
-- 2026-08-17. definition is classified source_body.
SELECT
    pipe_catalog,
    pipe_schema,
    pipe_name,
    pipe_owner,
    is_autoingest_enabled,
    notification_channel_name,
    definition,
    pattern,
    created,
    last_altered,
    comment
FROM snowflake.account_usage.pipes
WHERE deleted IS NULL
{scope_filter}
