-- md-assess-extract: query_shapes
-- Anonymous workload shapes: one row per distinct parameterized statement
-- structure (Snowflake's QUERY_PARAMETERIZED_HASH — an opaque token; no
-- query text is ever read or landed). The shape count is the size of the
-- dialect-rewrite worklist; per-shape weight shows what dominates the
-- workload. Cardinality is capped server-side at the top 5000 shapes by
-- total elapsed time; everything past the cap collapses into one explicit
-- '(remainder)' row per (query type, warehouse) — n_shapes says how many
-- collapsed — never silent truncation. Statements Snowflake does not hash
-- aggregate under an explicit '(unhashed)' bucket. Identifying a shape
-- happens on the customer's side in the guided session.
SELECT
    IFF(shape_rank <= 5000, shape_key, '(remainder)') AS shape_key,
    IFF(shape_rank <= 5000, hash_version, NULL) AS query_parameterized_hash_version,
    query_type AS query_type,
    warehouse_name AS warehouse_name,
    count(*) AS n_shapes,
    sum(n_queries) AS n_queries,
    sum(sum_elapsed_ms) AS sum_elapsed_ms,
    sum(sum_bytes_scanned) AS sum_bytes_scanned,
    min(first_seen) AS first_seen,
    max(last_seen) AS last_seen
FROM (
    SELECT
        COALESCE(query_parameterized_hash, '(unhashed)') AS shape_key,
        query_parameterized_hash_version AS hash_version,
        query_type,
        warehouse_name,
        count(*) AS n_queries,
        sum(total_elapsed_time) AS sum_elapsed_ms,
        sum(bytes_scanned) AS sum_bytes_scanned,
        min(start_time) AS first_seen,
        max(start_time) AS last_seen,
        ROW_NUMBER() OVER (ORDER BY sum(total_elapsed_time) DESC NULLS LAST) AS shape_rank
    FROM snowflake.account_usage.query_history
    WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
    AND COALESCE(is_client_generated_statement, FALSE) = FALSE
    {scope_filter}
    GROUP BY 1, 2, 3, 4
)
GROUP BY 1, 2, 3, 4
