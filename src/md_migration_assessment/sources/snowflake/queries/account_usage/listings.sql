-- Data-sharing surface: listings covers both marketplace/private listings and
-- their underlying shares. Column list verified live 2026-08-17.
SELECT
    name,
    global_name,
    owner,
    title,
    state,
    is_share,
    is_application,
    distribution,
    share,
    application_package,
    created_on,
    updated_on,
    published_on
FROM snowflake.account_usage.listings
WHERE deleted_on IS NULL
{scope_filter}
