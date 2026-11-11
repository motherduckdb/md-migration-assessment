-- External locations: a storage URL tied to a storage credential,
-- and whether it is read only.
SELECT external_location_name, url, storage_credential_name, read_only,
       external_location_owner, created, comment
FROM system.information_schema.external_locations
WHERE true
{scope_filter}
