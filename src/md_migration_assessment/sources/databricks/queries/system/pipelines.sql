-- md-assess-extract: pipelines
-- Lakeflow declarative pipelines (including the ones that back materialized
-- views and streaming tables) from system.lakeflow.pipelines: the latest
-- row per pipeline. Configuration and run-as are not collected.
SELECT
    pipeline_id AS pipeline_id,
    name AS pipeline_name,
    pipeline_type AS pipeline_type,
    settings.serverless AS serverless,
    settings.continuous AS continuous,
    settings.edition AS edition,
    create_time AS create_time,
    delete_time AS delete_time
FROM system.lakeflow.pipelines
WHERE true {workspace_filter}
QUALIFY row_number() OVER (PARTITION BY workspace_id, pipeline_id ORDER BY change_time DESC) = 1
