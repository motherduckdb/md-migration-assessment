-- Column masks per table: the masked column and the function that masks it.
SELECT table_catalog, table_schema, table_name, column_name, mask_name, using_columns
FROM {database}.information_schema.column_masks
WHERE true
{scope_filter}
