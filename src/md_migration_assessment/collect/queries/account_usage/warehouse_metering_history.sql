-- Hourly credit consumption per warehouse: the Snowflake current-state spend
-- baseline (spec §5.3). One row per warehouse-hour with activity; size scales
-- with warehouse count, not query volume. {scope_filter} is a no-op here —
-- warehouse spend has no database residency to scope by.
SELECT
    start_time,
    end_time,
    warehouse_name,
    credits_used,
    credits_used_compute,
    credits_used_cloud_services
FROM snowflake.account_usage.warehouse_metering_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
