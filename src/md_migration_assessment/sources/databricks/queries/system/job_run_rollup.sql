-- md-assess-extract: job_run_rollup
-- Job runs per (job, day, trigger, run type) from system.lakeflow.job_run_timeline.
-- A long run spans several timeline rows, so runs are counted distinct and
-- durations summed over the slices. No parameters or run names.
SELECT
    job_id AS job_id,
    CAST(period_start_time AS DATE) AS usage_date,
    trigger_type AS trigger_type,
    run_type AS run_type,
    count(DISTINCT run_id) AS n_runs,
    count(DISTINCT CASE WHEN result_state = 'SUCCEEDED' THEN run_id END) AS n_succeeded,
    count(DISTINCT CASE WHEN result_state IN ('FAILED', 'ERROR', 'TIMED_OUT') THEN run_id END) AS n_failed,
    sum(unix_timestamp(period_end_time) - unix_timestamp(period_start_time)) AS sum_run_seconds
FROM system.lakeflow.job_run_timeline
WHERE period_start_time >= current_timestamp() - INTERVAL {window_days} DAYS
GROUP BY 1, 2, 3, 4
