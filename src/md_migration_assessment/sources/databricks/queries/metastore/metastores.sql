SELECT metastore_name, cloud, region, privilege_model_version, delta_sharing_scope
FROM system.information_schema.metastores
WHERE true
{scope_filter}
