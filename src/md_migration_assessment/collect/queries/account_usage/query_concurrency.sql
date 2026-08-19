-- md-assess-extract: query_concurrency
-- Exact concurrency profile per warehouse-hour, computed entirely inside
-- Snowflake (spec decision 16): each query contributes a +1 event at its
-- exact start timestamp and a -1 event at its exact end timestamp, treating
-- queries as [start, end) intervals — same-instant events are netted by
-- grouping on the exact timestamp, so a query ending the instant another
-- starts never reads as overlap. A zero-delta event is materialized at every
-- hour boundary in the window (per warehouse seen in the window), so the
-- running level is carried into hours with no query events: every hour in
-- the window gets a row, including hours spanned by one long query and
-- fully idle hours (peak 0). The running sum over ordered events is the
-- exact step function of concurrently running queries; its hourly max is
-- the true peak. avg_concurrent_queries is time-weighted over the observed
-- span; busy_seconds is the time within the hour with >= 1 running query.
-- Output scales with wall-clock hours x warehouses, never query volume.
-- Caveat: queries that started before the window contribute no events, so
-- peaks near the window start are floors.
SELECT
    warehouse_name AS warehouse_name,
    DATE_TRUNC('HOUR', event_ts) AS hour_start,
    max(concurrent_queries) AS peak_concurrent_queries,
    (sum(concurrent_queries * segment_seconds)
        / NULLIF(sum(segment_seconds), 0)) AS avg_concurrent_queries,
    sum(IFF(concurrent_queries > 0, segment_seconds, 0)) AS busy_seconds
FROM (
    SELECT
        warehouse_name,
        event_ts,
        concurrent_queries,
        DATEDIFF('millisecond', event_ts,
                 COALESCE(LEAD(event_ts) OVER (
                     PARTITION BY warehouse_name ORDER BY event_ts),
                     event_ts)) / 1000.0 AS segment_seconds
    FROM (
        SELECT
            warehouse_name,
            event_ts,
            SUM(SUM(delta)) OVER (
                PARTITION BY warehouse_name ORDER BY event_ts
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS concurrent_queries
        FROM (
            SELECT warehouse_name, start_time AS event_ts, 1 AS delta
            FROM snowflake.account_usage.query_history
            WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
            AND warehouse_name IS NOT NULL
            AND COALESCE(is_client_generated_statement, FALSE) = FALSE
            {scope_filter}
            UNION ALL
            SELECT warehouse_name, COALESCE(end_time, CURRENT_TIMESTAMP), -1
            FROM snowflake.account_usage.query_history
            WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
            AND warehouse_name IS NOT NULL
            AND COALESCE(is_client_generated_statement, FALSE) = FALSE
            UNION ALL
            -- hour-boundary carriers: ROWCOUNT is a constant (366 days of
            -- hours, the --history-days maximum); the WHERE clips to the
            -- actual window
            SELECT
                w.warehouse_name,
                DATEADD(hour, g.hour_offset,
                        DATE_TRUNC('HOUR', DATEADD(day, -{window_days}, CURRENT_TIMESTAMP))),
                0
            FROM (
                SELECT DISTINCT warehouse_name
                FROM snowflake.account_usage.query_history
                WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
                AND warehouse_name IS NOT NULL
                AND COALESCE(is_client_generated_statement, FALSE) = FALSE
            ) w
            CROSS JOIN (
                SELECT ROW_NUMBER() OVER (ORDER BY seq4()) - 1 AS hour_offset
                FROM TABLE(GENERATOR(ROWCOUNT => 8784))
            ) g
            WHERE DATEADD(hour, g.hour_offset,
                          DATE_TRUNC('HOUR', DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)))
                  <= CURRENT_TIMESTAMP
        )
        GROUP BY warehouse_name, event_ts
    )
)
GROUP BY 1, 2
