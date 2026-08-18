-- Outbound share inventory (Snowgrid surface). ACCOUNT_USAGE.SHARES covers
-- outbound shares only; inbound shares need SHOW SHARES (a planned signal).
-- Column list verified live 2026-08-18.
SELECT
    name,
    owner,
    database_name,
    secure_objects_only,
    target_accounts,
    listing_global_name,
    comment,
    created_on,
    modified_on
FROM snowflake.account_usage.shares
WHERE deleted_on IS NULL
{scope_filter}
