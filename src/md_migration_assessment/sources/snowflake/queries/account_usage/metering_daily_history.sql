-- Daily credits by service type (WAREHOUSE_METERING, SERVERLESS_TASK,
-- PIPE, AUTO_CLUSTERING, ...): the account-level spend breakdown that
-- catches non-warehouse compute the per-warehouse metering view misses.
SELECT
    usage_date,
    service_type,
    credits_used_compute,
    credits_used_cloud_services,
    credits_used,
    credits_adjustment_cloud_services,
    credits_billed
FROM snowflake.account_usage.metering_daily_history
WHERE usage_date >= DATEADD(day, -{window_days}, CURRENT_DATE)
{scope_filter}
