-- Warehouse load, aggregated to warehouse-hour: the coarse concurrency
-- profile (spec §3 as amended 2026-08-19 — per-minute peaks are per-query
-- evidence and deferred to M3c). The source view reports sub-hourly interval
-- averages; aggregating here bounds volume regardless of account activity.
-- peak_avg_running is the highest interval average within the hour — an
-- interval-averaged floor for the true peak, not the peak itself.
SELECT
    DATE_TRUNC('HOUR', start_time) AS hour_start,
    warehouse_name AS warehouse_name,
    avg(avg_running) AS avg_running,
    avg(avg_queued_load) AS avg_queued_load,
    avg(avg_queued_provisioning) AS avg_queued_provisioning,
    avg(avg_blocked) AS avg_blocked,
    max(avg_running) AS peak_avg_running,
    count(*) AS n_intervals
FROM snowflake.account_usage.warehouse_load_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY 1, 2
