-- md-assess-extract: query_concurrency
-- Peak concurrent statements per (warehouse, hour) from system.query.history,
-- computed inside Databricks: +1 at each start, -1 at each end, same-instant
-- events netted, running sum per warehouse. A zero event at every hour start
-- carries the running count into hours where statements were active but
-- none started or ended. Only finished statements on a SQL warehouse count.
-- Statements that started before the window add no events, so peaks near
-- the window start are floors. Written as nested subqueries (no WITH) so the
-- outer SELECT is the one the handoff reads its column list from.
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
            {workspace_filter}
            AND compute.warehouse_id IS NOT NULL AND end_time IS NOT NULL
            UNION ALL
            SELECT compute.warehouse_id, end_time, -1
            FROM system.query.history
            WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
            {workspace_filter}
            AND compute.warehouse_id IS NOT NULL AND end_time IS NOT NULL
            UNION ALL
            -- a zero event at each hour start between the first start and last end
            SELECT warehouse_id, explode(sequence(lo, hi, INTERVAL 1 HOUR)), 0
            FROM (
                SELECT compute.warehouse_id AS warehouse_id,
                       date_trunc('HOUR', min(start_time)) AS lo,
                       date_trunc('HOUR', max(end_time)) AS hi
                FROM system.query.history
                WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
                {workspace_filter}
                AND compute.warehouse_id IS NOT NULL AND end_time IS NOT NULL
                GROUP BY 1
            ) span
        ) ev
        GROUP BY 1, 2
    ) net
) lvl
GROUP BY 1, 2
