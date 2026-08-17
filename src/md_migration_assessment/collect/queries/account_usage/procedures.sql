-- procedure_definition is classified source_body (see views.sql note).
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
FROM snowflake.account_usage.procedures
WHERE deleted IS NULL
{scope_filter}
