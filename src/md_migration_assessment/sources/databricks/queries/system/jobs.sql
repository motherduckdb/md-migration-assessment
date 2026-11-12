-- md-assess-extract: jobs
-- Lakeflow jobs from system.lakeflow.jobs: the latest row per job. Run-as,
-- creator, description and tags are not collected.
SELECT
    job_id AS job_id,
    name AS job_name,
    trigger_type AS trigger_type,
    paused AS paused,
    create_time AS create_time,
    delete_time AS delete_time
FROM system.lakeflow.jobs
WHERE true {workspace_filter}
QUALIFY row_number() OVER (PARTITION BY workspace_id, job_id ORDER BY change_time DESC) = 1
