SELECT
    usage_date,
    database_name,
    average_database_bytes,
    average_failsafe_bytes
FROM snowflake.account_usage.database_storage_usage_history
WHERE usage_date >= DATEADD(day, -{window_days}, CURRENT_DATE)
  AND deleted IS NULL
{scope_filter}
