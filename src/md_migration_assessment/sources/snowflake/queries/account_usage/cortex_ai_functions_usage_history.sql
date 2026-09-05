-- Column selection informed by google/dwh-migration-tools (Apache-2.0).
-- Cortex/AI feature usage, aggregated per function+model: the current view
-- (CORTEX_AI_FUNCTIONS_USAGE_HISTORY — the older CORTEX_FUNCTIONS_USAGE_HISTORY
-- is no longer updated) is per-query and carries user/query attribution the
-- feature inventory does not need. The report contract counts affected
-- objects, not events. Column list verified live 2026-08-18.
SELECT
    function_name AS function_name,
    model_name AS model_name,
    count(*) AS n_queries,
    sum(credits) AS total_credits,
    min(start_time) AS first_seen,
    max(start_time) AS last_seen
FROM snowflake.account_usage.cortex_ai_functions_usage_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY function_name, model_name
