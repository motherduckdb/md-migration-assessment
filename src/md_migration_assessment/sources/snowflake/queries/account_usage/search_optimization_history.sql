-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
-- Search-optimization usage, aggregated per table: the raw view is one row
-- per maintenance operation, and the report contract counts affected objects,
-- not events. NOTE: Snowflake documents TABLE_NAME here as an ID-based alias,
-- not necessarily the base table's display name. Enterprise Edition only.
-- Verified live 2026-08-18.
SELECT
    database_name AS database_name,
    schema_name AS schema_name,
    table_name AS table_name,
    count(*) AS n_operations,
    sum(credits_used) AS total_credits,
    min(start_time) AS first_seen,
    max(start_time) AS last_seen
FROM snowflake.account_usage.search_optimization_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY database_name, schema_name, table_name
