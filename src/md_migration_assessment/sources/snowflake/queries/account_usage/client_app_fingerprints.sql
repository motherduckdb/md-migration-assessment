-- md-assess-extract: client_app_fingerprints
-- Client/driver attribution of workload, per (client application, warehouse,
-- day): which drivers and tools actually run queries, at what weight —
-- deeper than the login-history inventory, which only sees connections.
-- CLIENT_APPLICATION_ID lives on ACCOUNT_USAGE.SESSIONS (SECURITY_VIEWER),
-- joined by session id; it is Snowflake-reported software identity
-- (e.g. 'JDBC 3.13.30'), not user content. A NULL client application means
-- the session row was not visible or not yet available. User identities
-- land only as a distinct count, never as names.
SELECT
    s.client_application_id AS client_application_id,
    q.warehouse_name AS warehouse_name,
    CAST(q.start_time AS DATE) AS usage_date,
    count(*) AS n_queries,
    count(DISTINCT q.user_name) AS n_distinct_users,
    sum(q.total_elapsed_time) AS sum_elapsed_ms,
    sum(q.bytes_scanned) AS sum_bytes_scanned,
    min(q.start_time) AS first_seen,
    max(q.start_time) AS last_seen
FROM snowflake.account_usage.query_history q
LEFT JOIN snowflake.account_usage.sessions s
    ON s.session_id = q.session_id
WHERE q.start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
AND COALESCE(q.is_client_generated_statement, FALSE) = FALSE
{scope_filter}
GROUP BY 1, 2, 3
