-- md-assess-extract: query_tag_fingerprints
-- Tool fingerprints from query tags, aggregated per (tool, warehouse, day).
-- QUERY_TAG is free text and can contain anything, so the raw tag value is
-- NEVER projected: rows carry only the derived tool label — recognized
-- patterns map to a tool name, everything else collapses to 'other_tagged'
-- (privacy by projection, spec §4). Untagged queries are excluded here;
-- client_app_fingerprints covers them. User identities land only as a
-- distinct count, never as names.
SELECT
    CASE
        WHEN query_tag ILIKE '%dbt%' THEN 'dbt'
        WHEN query_tag ILIKE '%tableau%' THEN 'tableau'
        WHEN query_tag ILIKE '%looker%' THEN 'looker'
        WHEN query_tag ILIKE '%sigma%' THEN 'sigma'
        WHEN query_tag ILIKE '%metabase%' THEN 'metabase'
        WHEN query_tag ILIKE '%power bi%' OR query_tag ILIKE '%powerbi%' THEN 'power_bi'
        WHEN query_tag ILIKE '%hex%' THEN 'hex'
        WHEN query_tag ILIKE '%fivetran%' THEN 'fivetran'
        WHEN query_tag ILIKE '%airbyte%' THEN 'airbyte'
        WHEN query_tag ILIKE '%airflow%' THEN 'airflow'
        WHEN query_tag ILIKE '%dagster%' THEN 'dagster'
        ELSE 'other_tagged'
    END AS query_tag_tool,
    warehouse_name AS warehouse_name,
    CAST(start_time AS DATE) AS usage_date,
    count(*) AS n_queries,
    count(DISTINCT user_name) AS n_distinct_users,
    sum(total_elapsed_time) AS sum_elapsed_ms,
    sum(bytes_scanned) AS sum_bytes_scanned,
    min(start_time) AS first_seen,
    max(start_time) AS last_seen
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
AND COALESCE(is_client_generated_statement, FALSE) = FALSE
AND query_tag IS NOT NULL AND query_tag <> ''
{scope_filter}
GROUP BY 1, 2, 3
