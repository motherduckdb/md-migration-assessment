-- md-assess-extract: grants_to_roles_summary
-- RBAC complexity, aggregated INSIDE Snowflake (M3d, decision 18): per-role
-- privilege/object/database COUNTS sizing the role lattice — the raw grant
-- edge list is unbounded and identity-dense and never lands. Only role names
-- (already collected by the roles extract) appear; object names arrive as
-- counts only.
SELECT
    grantee_name AS role_name,
    granted_on AS granted_on,
    count(*) AS n_grants,
    count(DISTINCT privilege) AS n_privileges,
    count(DISTINCT name) AS n_objects,
    count(DISTINCT table_catalog) AS n_databases,
    max(created_on) AS last_grant_created
FROM snowflake.account_usage.grants_to_roles
WHERE deleted_on IS NULL
{scope_filter}
GROUP BY 1, 2
