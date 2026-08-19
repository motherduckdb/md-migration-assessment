-- md-assess-extract: query_concurrency
-- Exact concurrency profile per warehouse-hour, computed entirely inside
-- Snowflake (spec decision 16): each query contributes a +1 event at its
-- exact start timestamp and a -1 event at its exact end timestamp, treating
-- queries as [start, end) intervals — same-instant events are netted by
-- grouping on the exact timestamp, so a query ending the instant another
-- starts never reads as overlap. The running sum over ordered events is the
-- exact step function of concurrently running queries; its hourly max is
-- the true peak.
--
-- Observation window: [window start, CURRENT_TIMESTAMP - 45 minutes].
-- ACCOUNT_USAGE.QUERY_HISTORY lags up to 45 minutes (docs, pinned
-- 2026-08-19), so the spine ends at that watermark: the latency gap emits
-- NO rows rather than fabricated idle ones (missing evidence is never an
-- observed zero), and the runner reports the watermark as
-- actual_window_end via the manifest's window_end_lag_minutes = 45.
--
-- Zero-delta carrier events bracket the observation exactly: one at the
-- exact window start, one at the exact watermark, and one at every hour
-- boundary between them (a constant-ROWCOUNT generator clamped by
-- GREATEST/LEAST — clamp duplicates net out in the event grouping). Every
-- observed hour therefore gets a row (including idle ones, peak 0), levels
-- carry across hours spanned by one long query, and the partial first/last
-- hours average over exactly the observed span: avg_concurrent_queries is
-- time-weighted over observed seconds in the hour, busy_seconds is the
-- observed time with >= 1 running query. End events of still-running
-- queries clamp to the watermark. Output scales with wall-clock hours x
-- warehouses, never query volume. Caveat: queries that started before the
-- window contribute no events, so peaks near the window start are floors.
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
            AND start_time < DATEADD(minute, -45, CURRENT_TIMESTAMP)
            AND warehouse_name IS NOT NULL
            AND COALESCE(is_client_generated_statement, FALSE) = FALSE
            {scope_filter}
            UNION ALL
            SELECT
                warehouse_name,
                LEAST(COALESCE(end_time, CURRENT_TIMESTAMP),
                      DATEADD(minute, -45, CURRENT_TIMESTAMP)),
                -1
            FROM snowflake.account_usage.query_history
            WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
            AND start_time < DATEADD(minute, -45, CURRENT_TIMESTAMP)
            AND warehouse_name IS NOT NULL
            AND COALESCE(is_client_generated_statement, FALSE) = FALSE
            UNION ALL
            -- observation carriers: hour boundaries clamped into
            -- [window start, watermark]; the clamps produce the exact
            -- start/end brackets, and clamp duplicates net to one event
            SELECT
                w.warehouse_name,
                GREATEST(DATEADD(day, -{window_days}, CURRENT_TIMESTAMP),
                         LEAST(DATEADD(hour, g.hour_offset,
                                       DATE_TRUNC('HOUR', DATEADD(day, -{window_days}, CURRENT_TIMESTAMP))),
                               DATEADD(minute, -45, CURRENT_TIMESTAMP))),
                0
            FROM (
                SELECT DISTINCT warehouse_name
                FROM snowflake.account_usage.query_history
                WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
                AND start_time < DATEADD(minute, -45, CURRENT_TIMESTAMP)
                AND warehouse_name IS NOT NULL
                AND COALESCE(is_client_generated_statement, FALSE) = FALSE
            ) w
            CROSS JOIN (
                SELECT ROW_NUMBER() OVER (ORDER BY seq4()) - 1 AS hour_offset
                FROM TABLE(GENERATOR(ROWCOUNT => 8786))
            ) g
        )
        GROUP BY warehouse_name, event_ts
    )
)
GROUP BY 1, 2
