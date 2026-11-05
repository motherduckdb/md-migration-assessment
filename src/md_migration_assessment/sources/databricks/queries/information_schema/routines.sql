SELECT routine_catalog, routine_schema, routine_name, routine_type, routine_body,
       external_language, data_type, is_deterministic, security_type,
       routine_owner, created, last_altered, routine_definition, comment
FROM {database}.information_schema.routines
WHERE routine_schema <> 'information_schema'
{scope_filter}
