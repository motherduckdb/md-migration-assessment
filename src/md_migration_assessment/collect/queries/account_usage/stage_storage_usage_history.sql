SELECT
    usage_date,
    average_stage_bytes
FROM snowflake.account_usage.stage_storage_usage_history
WHERE usage_date >= DATEADD(day, -{window_days}, CURRENT_DATE)
{scope_filter}
