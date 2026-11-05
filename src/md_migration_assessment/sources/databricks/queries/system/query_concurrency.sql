-- md-assess-extract: query_concurrency
-- Peak concurrent statements per (warehouse, hour) from system.query.history,
-- computed inside Databricks: +1 at each start, -1 at each end, same-instant
-- events netted, running sum per warehouse. Only finished statements on a
-- SQL warehouse count. Statements that started before the window add no
-- events, so peaks near the window start are floors.
SELECT
    warehouse_id AS warehouse_id,
    date_trunc('HOUR', ts) AS hour_start,
    max(concurrent) AS peak_concurrent_queries,
    sum(n_started) AS n_started
FROM (
    SELECT warehouse_id, ts, n_started,
        sum(d) OVER (PARTITION BY warehouse_id ORDER BY ts
                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS concurrent
    FROM (
        SELECT warehouse_id, ts, sum(d) AS d,
               sum(CASE WHEN d > 0 THEN 1 ELSE 0 END) AS n_started
        FROM (
            SELECT compute.warehouse_id AS warehouse_id, start_time AS ts, 1 AS d
            FROM system.query.history
            WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
            AND compute.warehouse_id IS NOT NULL AND end_time IS NOT NULL
            UNION ALL
            SELECT compute.warehouse_id, end_time, -1
            FROM system.query.history
            WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
            AND compute.warehouse_id IS NOT NULL AND end_time IS NOT NULL
        ) ev
        GROUP BY 1, 2
    ) net
) lvl
GROUP BY 1, 2
