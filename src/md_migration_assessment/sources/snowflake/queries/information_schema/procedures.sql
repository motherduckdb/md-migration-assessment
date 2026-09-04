-- Column selection informed by google/dwh-migration-tools (Apache-2.0),
-- dumper snowflake connector.
SELECT
    procedure_catalog,
    procedure_schema,
    procedure_name,
    procedure_owner,
    procedure_language,
    argument_signature,
    data_type,
    procedure_definition,
    created,
    last_altered,
    comment
FROM {database}.information_schema.procedures
WHERE procedure_schema <> 'INFORMATION_SCHEMA'
{scope_filter}