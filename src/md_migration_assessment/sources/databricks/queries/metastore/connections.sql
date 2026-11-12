-- Lakehouse Federation connections. connection_options and url are never
-- selected: they can carry hosts and secret references.
SELECT connection_name, connection_type, credential_type, connection_owner,
       created, comment
FROM system.information_schema.connections
WHERE true
{scope_filter}
