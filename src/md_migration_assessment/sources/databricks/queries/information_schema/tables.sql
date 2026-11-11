-- Every table, view and materialized view in each catalog: type,
-- format, storage path and owner.
SELECT table_catalog, table_schema, table_name, table_type, data_source_format,
       storage_path, table_owner, created, created_by, last_altered, comment
FROM {database}.information_schema.tables
WHERE table_schema <> 'information_schema'
{scope_filter}
