-- Pipe usage aggregated per (pipe, day, kind): credits and volume per named
-- pipe — pairs with copy_history (per-table) to size the ingestion
-- re-pointing work. NULL-name rows are Snowflake's hidden auto-refresh pipes
-- (external table / Delta-based Iceberg metadata refresh — docs, pinned
-- 2026-08-19): excluded here, spend visible in metering_daily_history.
-- A non-NULL PIPE_NAME is documented as either a pipe OR an Iceberg table
-- with automated refresh, so rows are classified against the account's
-- actual pipe objects (ACCOUNT_USAGE.PIPES shares the PIPE_ID keyspace and
-- retains dropped pipes): matched rows are 'snowpipe'; unmatched rows —
-- Iceberg automated refresh, or a pipe aged out of the PIPES view — are
-- 'unclassified_refresh', never presumed Snowpipe workload.
-- Database/schema derive from the qualified name so --scope filtering holds
-- server-side (a quoted pipe identifier containing dots would mis-split and
-- be excluded — fail-closed, never a leak).
SELECT
    u.pipe_id AS pipe_id,
    split_part(u.pipe_name, '.', 1) AS pipe_database,
    split_part(u.pipe_name, '.', 2) AS pipe_schema,
    u.pipe_name AS pipe_name,
    IFF(p.pipe_id IS NOT NULL, 'snowpipe', 'unclassified_refresh') AS source_kind,
    CAST(u.start_time AS DATE) AS usage_date,
    sum(u.credits_used) AS credits_used,
    sum(u.bytes_inserted) AS bytes_inserted,
    sum(u.files_inserted) AS files_inserted,
    count(*) AS n_intervals
FROM snowflake.account_usage.pipe_usage_history u
LEFT JOIN snowflake.account_usage.pipes p
    ON p.pipe_id = u.pipe_id
WHERE u.start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
AND u.pipe_name IS NOT NULL
{scope_filter}
GROUP BY 1, 2, 3, 4, 5, 6
