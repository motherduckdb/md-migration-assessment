-- md-assess-extract: warehouses
-- SQL warehouses from system.compute.warehouses (one row per config change):
-- the latest row per warehouse. Tags and creator are not collected.
SELECT
    warehouse_id AS warehouse_id,
    warehouse_name AS warehouse_name,
    warehouse_type AS warehouse_type,
    warehouse_channel AS warehouse_channel,
    warehouse_size AS warehouse_size,
    min_clusters AS min_clusters,
    max_clusters AS max_clusters,
    auto_stop_minutes AS auto_stop_minutes,
    change_time AS change_time,
    delete_time AS delete_time
FROM system.compute.warehouses
WHERE true {workspace_filter}
QUALIFY row_number() OVER (PARTITION BY workspace_id, warehouse_id ORDER BY change_time DESC) = 1
