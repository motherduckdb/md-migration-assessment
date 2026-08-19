-- Snowpipe usage aggregated per (pipe, day): credits and volume per pipe —
-- pairs with copy_history (per-table) to size the ingestion re-pointing work.
-- PIPE_NAME here is the fully qualified pipe name.
SELECT
    pipe_name AS pipe_name,
    CAST(start_time AS DATE) AS usage_date,
    sum(credits_used) AS credits_used,
    sum(bytes_inserted) AS bytes_inserted,
    sum(files_inserted) AS files_inserted,
    count(*) AS n_intervals
FROM snowflake.account_usage.pipe_usage_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY 1, 2
