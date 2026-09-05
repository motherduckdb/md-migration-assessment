-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
-- Client/driver inventory aggregated per (user, client type, client version):
-- which drivers and tools must exist on the MotherDuck side. The source view
-- carries per-event CLIENT_IP and authentication factors — never selected,
-- never fetched (privacy by projection, spec §4): this aggregate retains no
-- per-login events at all.
SELECT
    user_name AS user_name,
    reported_client_type AS client_type,
    reported_client_version AS client_version,
    count(*) AS n_logins,
    count_if(is_success = 'YES') AS n_successful,
    min(event_timestamp) AS first_seen,
    max(event_timestamp) AS last_seen
FROM snowflake.account_usage.login_history
WHERE event_timestamp >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY 1, 2, 3
