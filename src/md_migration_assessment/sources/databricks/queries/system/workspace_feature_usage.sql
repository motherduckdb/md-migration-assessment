-- md-assess-extract: workspace_feature_usage
-- Which platform services are in use (notebooks, dashboards, alerts, apps,
-- Genie, repos, jobs, ...) per (service, action, day) from system.access.audit.
-- Users land only as a distinct count. Request parameters, IPs and user
-- agents are not read. On a large workspace this scans the window's audit
-- partitions (event_date), so it is the slowest extract.
SELECT
    service_name AS service_name,
    action_name AS action_name,
    event_date AS usage_date,
    count(*) AS n_events,
    count(DISTINCT user_identity.email) AS n_distinct_users
FROM system.access.audit
WHERE event_date >= CAST(current_timestamp() - INTERVAL {window_days} DAYS AS DATE)
GROUP BY 1, 2, 3
