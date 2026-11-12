-- md-assess-extract: workspaces
-- The workspaces this collection covers, from system.access.workspaces_latest.
-- The other system-table extracts are limited to the same workspaces.
SELECT
    workspace_id AS workspace_id,
    workspace_name AS workspace_name,
    workspace_url AS workspace_url,
    status AS status
FROM system.access.workspaces_latest
WHERE true {workspace_filter}
