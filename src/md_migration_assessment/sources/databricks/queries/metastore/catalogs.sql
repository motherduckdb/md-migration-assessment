SELECT catalog_name, catalog_owner, created, created_by, last_altered, comment
FROM system.information_schema.catalogs
WHERE catalog_name NOT IN ('system', '__databricks_internal')
{scope_filter}
