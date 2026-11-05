-- md-assess-extract: client_app_fingerprints
-- Which tools and drivers run the workload, per (client application, driver,
-- compute type, day) from system.query.history. Users land only as a
-- distinct count, never as names.
SELECT
    client_application AS client_application,
    client_driver AS client_driver,
    compute.type AS compute_type,
    CAST(start_time AS DATE) AS usage_date,
    count(*) AS n_queries,
    count(DISTINCT executed_by_user_id) AS n_distinct_users,
    sum(total_duration_ms) AS sum_duration_ms,
    sum(read_bytes) AS sum_read_bytes,
    min(start_time) AS first_seen,
    max(start_time) AS last_seen
FROM system.query.history
WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
GROUP BY 1, 2, 3, 4
