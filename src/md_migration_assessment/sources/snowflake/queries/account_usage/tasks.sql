-- Task inventory (orchestration surface). Column list verified live against
-- ACCOUNT_USAGE 2026-08-17. definition/condition are classified source_body.
SELECT
    task_database,
    task_schema,
    task_name,
    task_owner,
    warehouse,
    schedule,
    state,
    predecessors,
    definition,
    condition,
    created,
    last_altered,
    comment
FROM snowflake.account_usage.tasks
WHERE deleted IS NULL
{scope_filter}
