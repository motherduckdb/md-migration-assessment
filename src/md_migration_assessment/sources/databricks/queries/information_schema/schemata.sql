SELECT catalog_name, schema_name, schema_owner, created, created_by, last_altered, comment
FROM {database}.information_schema.schemata
WHERE schema_name <> 'information_schema'
{scope_filter}
