-- md-assess-extract: query_workload_rollup
-- Workload rollup per (warehouse, query type, day): the MD instance-sizing
-- inputs — volume, elapsed and bytes-scanned percentiles (APPROX_PERCENTILE,
-- computed inside Snowflake), spill rates (queries exceeding warehouse
-- memory), and queueing. Nothing per-query lands.
SELECT
    warehouse_name AS warehouse_name,
    query_type AS query_type,
    CAST(start_time AS DATE) AS usage_date,
    count(*) AS n_queries,
    count_if(execution_status = 'SUCCESS') AS n_succeeded,
    sum(total_elapsed_time) AS sum_elapsed_ms,
    APPROX_PERCENTILE(total_elapsed_time, 0.95) AS p95_elapsed_ms,
    sum(bytes_scanned) AS sum_bytes_scanned,
    APPROX_PERCENTILE(bytes_scanned, 0.5) AS p50_bytes_scanned,
    APPROX_PERCENTILE(bytes_scanned, 0.95) AS p95_bytes_scanned,
    count_if(bytes_spilled_to_local_storage > 0) AS n_spilled_local,
    count_if(bytes_spilled_to_remote_storage > 0) AS n_spilled_remote,
    sum(bytes_spilled_to_local_storage) AS sum_bytes_spilled_local,
    sum(bytes_spilled_to_remote_storage) AS sum_bytes_spilled_remote,
    sum(queued_overload_time) AS sum_queued_overload_ms
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
AND COALESCE(is_client_generated_statement, FALSE) = FALSE
{scope_filter}
GROUP BY 1, 2, 3
