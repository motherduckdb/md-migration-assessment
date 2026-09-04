-- Column list verified live against ACCOUNT_USAGE 2026-08-17.
SELECT
    tag_database,
    tag_schema,
    tag_name,
    tag_owner,
    allowed_values,
    propagate,
    created,
    last_altered,
    tag_comment
FROM snowflake.account_usage.tags
WHERE deleted IS NULL
{scope_filter}
