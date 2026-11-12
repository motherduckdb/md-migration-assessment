-- Storage credentials the collecting principal can see: name, type and owner.
-- The credential column (role ARN / secret reference) is never selected.
SELECT storage_credential_name, credential_type, used_for_managed_storage,
       storage_credential_owner, created, comment
FROM system.information_schema.storage_credentials
WHERE true
{scope_filter}
