-- The view->table dependency graph: migration ordering / dbt DAG input
-- (M3d, decision 18). Snapshot, catalog-bounded. Scope semantics match view
-- definitions: --scope selects which REFERENCING objects' rows are collected,
-- and what an in-scope object references is an attribute of that object —
-- referenced names may point outside the scope, exactly like the SQL text of
-- an in-scope view.
SELECT
    referencing_database AS referencing_database,
    referencing_schema AS referencing_schema,
    referencing_object_name AS referencing_object_name,
    referencing_object_domain AS referencing_object_domain,
    referenced_database AS referenced_database,
    referenced_schema AS referenced_schema,
    referenced_object_name AS referenced_object_name,
    referenced_object_domain AS referenced_object_domain
FROM snowflake.account_usage.object_dependencies
WHERE 1=1
{scope_filter}
