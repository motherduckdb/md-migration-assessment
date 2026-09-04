-- Observed dynamic-table refresh cadence, aggregated per (table, day) —
-- sizes the replacement schedule for dbt incremental / scheduled CTAS
-- (M3d, decision 18). Pairs with SHOW DYNAMIC TABLES' declared
-- target_lag/refresh_mode.
SELECT
    database_name AS table_database,
    schema_name AS table_schema,
    name AS table_name,
    CAST(refresh_start_time AS DATE) AS refresh_date,
    count(*) AS n_refreshes,
    count_if(state = 'SUCCEEDED') AS n_succeeded,
    count_if(state IN ('FAILED', 'UPSTREAM_FAILED')) AS n_failed,
    min(refresh_start_time) AS first_refresh,
    max(refresh_start_time) AS last_refresh
FROM snowflake.account_usage.dynamic_table_refresh_history
WHERE refresh_start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY 1, 2, 3, 4
