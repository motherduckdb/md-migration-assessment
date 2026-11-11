-- Delta Sharing shares the collecting principal can see, with owner and date.
SELECT share_name, share_owner, created, comment
FROM system.information_schema.shares
WHERE true
{scope_filter}
