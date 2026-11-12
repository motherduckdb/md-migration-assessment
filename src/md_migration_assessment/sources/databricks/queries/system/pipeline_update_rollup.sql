-- md-assess-extract: pipeline_update_rollup
-- Pipeline updates per (pipeline, day, update type, trigger) from
-- system.lakeflow.pipeline_update_timeline, counted distinct across slices.
SELECT
    pipeline_id AS pipeline_id,
    CAST(period_start_time AS DATE) AS usage_date,
    update_type AS update_type,
    trigger_type AS trigger_type,
    count(DISTINCT update_id) AS n_updates,
    count(DISTINCT CASE WHEN result_state = 'COMPLETED' THEN update_id END) AS n_completed,
    count(DISTINCT CASE WHEN result_state = 'FAILED' THEN update_id END) AS n_failed,
    sum(unix_timestamp(period_end_time) - unix_timestamp(period_start_time)) AS sum_update_seconds
FROM system.lakeflow.pipeline_update_timeline
WHERE period_start_time >= current_timestamp() - INTERVAL {window_days} DAYS
{workspace_filter}
GROUP BY 1, 2, 3, 4
