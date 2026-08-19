-- Snowpipe usage aggregated per (pipe, day): credits and volume per named
-- pipe — pairs with copy_history (per-table) to size the ingestion
-- re-pointing work. PIPE_NAME is the fully qualified name; NULL-name rows
-- are Snowflake's hidden auto-refresh pipes (external table / Delta-based
-- Iceberg metadata refresh — docs, pinned 2026-08-19): not Snowpipe
-- workload and with no database residency to scope by, they are excluded
-- here; their spend stays visible in metering_daily_history service types.
-- Database/schema derive from the qualified name so --scope filtering holds
-- server-side (a quoted pipe identifier containing dots would mis-split and
-- be excluded — fail-closed, never a leak).
SELECT
    pipe_id AS pipe_id,
    split_part(pipe_name, '.', 1) AS pipe_database,
    split_part(pipe_name, '.', 2) AS pipe_schema,
    pipe_name AS pipe_name,
    CAST(start_time AS DATE) AS usage_date,
    sum(credits_used) AS credits_used,
    sum(bytes_inserted) AS bytes_inserted,
    sum(files_inserted) AS files_inserted,
    count(*) AS n_intervals
FROM snowflake.account_usage.pipe_usage_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
AND pipe_name IS NOT NULL
{scope_filter}
GROUP BY 1, 2, 3, 4, 5
