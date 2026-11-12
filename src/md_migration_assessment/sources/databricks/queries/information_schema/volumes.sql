-- Volumes (managed file storage) in each catalog, with type, location and owner.
SELECT volume_catalog, volume_schema, volume_name, volume_type, storage_location,
       volume_owner, created, comment
FROM {database}.information_schema.volumes
WHERE true
{scope_filter}
