-- md-assess-extract: query_concurrency
-- True concurrency profile per warehouse-hour, computed entirely inside
-- Snowflake (spec decision 16): each query contributes a +1 event at its
-- start minute and a -1 event after its end minute; the running sum over
-- event minutes is the exact step function of concurrently running queries,
-- and its hourly max is the true peak (peaks only occur at start events).
-- Output scales with wall-clock hours x warehouses, never query volume.
-- Caveats: queries that started before the window contribute no +1, so
-- early-window peaks are floors; Snowflake-internal statements are excluded.
SELECT
    warehouse_name AS warehouse_name,
    DATE_TRUNC('HOUR', event_minute) AS hour_start,
    max(concurrent_queries) AS peak_concurrent_queries,
    count_if(concurrent_queries > 0) AS active_event_minutes
FROM (
    SELECT
        warehouse_name,
        event_minute,
        SUM(SUM(delta)) OVER (
            PARTITION BY warehouse_name ORDER BY event_minute
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS concurrent_queries
    FROM (
        SELECT
            warehouse_name,
            DATE_TRUNC('MINUTE', start_time) AS event_minute,
            1 AS delta
        FROM snowflake.account_usage.query_history
        WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
        AND warehouse_name IS NOT NULL
        AND COALESCE(is_client_generated_statement, FALSE) = FALSE
        {scope_filter}
        UNION ALL
        SELECT
            warehouse_name,
            DATEADD(minute, 1, DATE_TRUNC('MINUTE', COALESCE(end_time, CURRENT_TIMESTAMP))),
            -1
        FROM snowflake.account_usage.query_history
        WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
        AND warehouse_name IS NOT NULL
        AND COALESCE(is_client_generated_statement, FALSE) = FALSE
    )
    GROUP BY warehouse_name, event_minute
)
GROUP BY 1, 2
