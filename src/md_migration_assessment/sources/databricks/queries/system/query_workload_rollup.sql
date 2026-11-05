-- md-assess-extract: query_workload_rollup
-- Workload rollup per (compute, statement type, day) from system.query.history:
-- volume, duration and bytes-read percentiles (computed inside Databricks),
-- result-cache hits, spill and queueing. Nothing per-query lands: no
-- statement text, users or parameters.
SELECT
    compute.type AS compute_type,
    compute.warehouse_id AS warehouse_id,
    statement_type AS statement_type,
    CAST(start_time AS DATE) AS usage_date,
    count(*) AS n_queries,
    count_if(execution_status = 'FINISHED') AS n_succeeded,
    count_if(from_result_cache) AS n_from_result_cache,
    sum(total_duration_ms) AS sum_duration_ms,
    percentile_approx(total_duration_ms, 0.95) AS p95_duration_ms,
    sum(read_bytes) AS sum_read_bytes,
    percentile_approx(read_bytes, 0.5) AS p50_read_bytes,
    percentile_approx(read_bytes, 0.95) AS p95_read_bytes,
    sum(read_rows) AS sum_read_rows,
    sum(written_bytes) AS sum_written_bytes,
    count_if(spilled_local_bytes > 0) AS n_spilled_local,
    sum(spilled_local_bytes) AS sum_spilled_local_bytes,
    sum(waiting_at_capacity_duration_ms) AS sum_waiting_at_capacity_ms,
    sum(waiting_for_compute_duration_ms) AS sum_waiting_for_compute_ms
FROM system.query.history
WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
{scope_filter}
GROUP BY 1, 2, 3, 4
