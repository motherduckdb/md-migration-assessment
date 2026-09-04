-- Snowpipe Streaming usage, aggregated per client: the raw view is
-- per-blob-event and unbounded, but feature evidence only needs the client
-- inventory and volume. Verified live 2026-08-18.
SELECT
    client_name AS client_name,
    min(event_timestamp) AS first_seen,
    max(event_timestamp) AS last_seen,
    count(*) AS n_events,
    sum(blob_size_bytes) AS total_blob_bytes
FROM snowflake.account_usage.snowpipe_streaming_client_history
WHERE event_timestamp >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY client_name
