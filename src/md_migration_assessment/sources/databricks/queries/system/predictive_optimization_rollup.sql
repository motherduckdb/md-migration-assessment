-- md-assess-extract: predictive_optimization_rollup
-- Predictive optimization runs (compaction, clustering, vacuum, analyze)
-- per (operation type, status, day) from
-- system.storage.predictive_optimization_operations_history.
SELECT
    operation_type AS operation_type,
    operation_status AS operation_status,
    CAST(start_time AS DATE) AS usage_date,
    count(*) AS n_operations,
    count(DISTINCT table_id) AS n_tables,
    sum(usage_quantity) AS usage_quantity
FROM system.storage.predictive_optimization_operations_history
WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
GROUP BY 1, 2, 3
