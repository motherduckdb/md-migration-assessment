-- md-assess-extract: table_lineage_rollup
-- Table-to-table lineage edges from system.access.table_lineage, rolled up
-- per (source, target, entity type): which tables feed which, through what
-- (notebook, job, pipeline, DBSQL query, dashboard), and how often. Rows
-- with no target are reads, so this is also the read-heat map. File paths,
-- users and statement ids are not collected. With --scope, an edge is kept
-- only when each table it names is in scope, so no out-of-scope name is stored.
SELECT
    source_table_full_name AS source_table_full_name,
    source_type AS source_type,
    target_table_full_name AS target_table_full_name,
    target_type AS target_type,
    entity_type AS entity_type,
    count(*) AS n_events,
    count(DISTINCT event_date) AS n_active_days,
    min(event_date) AS first_seen,
    max(event_date) AS last_seen
FROM system.access.table_lineage
WHERE event_date >= CAST(current_timestamp() - INTERVAL {window_days} DAYS AS DATE)
{workspace_filter}
AND (source_table_full_name IS NOT NULL OR target_table_full_name IS NOT NULL)
AND (source_table_full_name IS NULL OR {scope_match:source_table_catalog:source_table_schema})
AND (target_table_full_name IS NULL OR {scope_match:target_table_catalog:target_table_schema})
GROUP BY 1, 2, 3, 4, 5
