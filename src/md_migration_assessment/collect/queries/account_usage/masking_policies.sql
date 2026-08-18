-- Column list verified live against ACCOUNT_USAGE 2026-08-17.
-- policy_body is classified source_body.
SELECT
    policy_catalog,
    policy_schema,
    policy_name,
    policy_owner,
    policy_signature,
    policy_return_type,
    policy_body,
    options,
    created,
    last_altered,
    policy_comment
FROM snowflake.account_usage.masking_policies
WHERE deleted IS NULL
{scope_filter}
