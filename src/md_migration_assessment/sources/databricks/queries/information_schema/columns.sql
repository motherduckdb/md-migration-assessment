SELECT table_catalog, table_schema, table_name, column_name, ordinal_position,
       data_type, full_data_type, is_nullable, partition_index, comment
FROM {database}.information_schema.columns
WHERE table_schema <> 'information_schema'
{scope_filter}
