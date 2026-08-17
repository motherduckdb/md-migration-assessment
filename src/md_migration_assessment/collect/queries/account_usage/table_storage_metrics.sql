-- How the migration gets sized: active bytes vs time-travel/failsafe/clone
-- overhead per table (spec §5.2). Informed by google/dwh-migration-tools
-- (Apache-2.0).
SELECT
    table_catalog,
    table_schema,
    table_name,
    id,
    clone_group_id,
    is_transient,
    active_bytes,
    time_travel_bytes,
    failsafe_bytes,
    retained_for_clone_bytes,
    table_created,
    table_entered_failsafe,
    catalog_created,
    schema_created
FROM snowflake.account_usage.table_storage_metrics
WHERE NOT deleted
{scope_filter}
