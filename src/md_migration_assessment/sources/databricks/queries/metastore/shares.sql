SELECT share_name, share_owner, created, comment
FROM system.information_schema.shares
WHERE true
{scope_filter}
