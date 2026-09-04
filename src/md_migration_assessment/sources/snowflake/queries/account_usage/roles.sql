-- Role inventory (names/owners only — no grants in M2). Column list verified
-- live against ACCOUNT_USAGE 2026-08-17.
SELECT
    name,
    owner,
    role_type,
    role_database_name,
    created_on,
    comment
FROM snowflake.account_usage.roles
WHERE deleted_on IS NULL
{scope_filter}
