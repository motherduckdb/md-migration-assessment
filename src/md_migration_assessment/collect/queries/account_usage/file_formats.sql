-- Named file-format inventory (M3d, decision 18): the COPY pipeline's format
-- assumptions. Maps to read_csv/json/parquet options on MotherDuck; XML is a
-- known gap the overlay flags.
SELECT
    file_format_catalog,
    file_format_schema,
    file_format_name,
    file_format_type,
    comment
FROM snowflake.account_usage.file_formats
WHERE deleted IS NULL
{scope_filter}
