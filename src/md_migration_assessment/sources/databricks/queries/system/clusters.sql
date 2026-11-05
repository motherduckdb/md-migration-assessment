-- md-assess-extract: clusters
-- Classic (all-purpose and job) clusters from system.compute.clusters: the
-- latest config row per cluster. Owner, tags and init scripts are not
-- collected; node types and worker counts size the Spark estate.
SELECT
    cluster_id AS cluster_id,
    cluster_name AS cluster_name,
    cluster_source AS cluster_source,
    driver_node_type AS driver_node_type,
    worker_node_type AS worker_node_type,
    worker_count AS worker_count,
    min_autoscale_workers AS min_autoscale_workers,
    max_autoscale_workers AS max_autoscale_workers,
    auto_termination_minutes AS auto_termination_minutes,
    dbr_version AS dbr_version,
    data_security_mode AS data_security_mode,
    create_time AS create_time,
    delete_time AS delete_time
FROM system.compute.clusters
QUALIFY row_number() OVER (PARTITION BY cluster_id ORDER BY change_time DESC) = 1
