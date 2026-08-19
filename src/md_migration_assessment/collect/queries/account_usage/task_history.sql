-- Task runs aggregated per (task, day): scheduled-pipeline cadence evidence.
-- The source view carries QUERY_TEXT and CONDITION_TEXT per run — neither is
-- ever selected: task SQL is query text, and this aggregate needs only run
-- counts and outcomes (privacy by projection, spec §4).
SELECT
    database_name AS task_database,
    schema_name AS task_schema,
    name AS task_name,
    CAST(scheduled_time AS DATE) AS run_date,
    count(*) AS n_runs,
    count_if(state = 'SUCCEEDED') AS n_succeeded,
    count_if(state = 'FAILED') AS n_failed,
    min(scheduled_time) AS first_scheduled_time,
    max(scheduled_time) AS last_scheduled_time
FROM snowflake.account_usage.task_history
WHERE scheduled_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY 1, 2, 3, 4
