-- Named file-format inventory (M3d) — lite-capable fallback.
SELECT
    file_format_catalog,
    file_format_schema,
    file_format_name,
    file_format_type,
    comment
FROM {database}.information_schema.file_formats
WHERE file_format_schema <> 'INFORMATION_SCHEMA'
{scope_filter}
