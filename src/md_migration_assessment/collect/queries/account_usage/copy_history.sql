-- Bulk-load evidence aggregated per (table, day, method): the authoritative
-- half of the ingestion inventory (spec §3) — COPY INTO and Snowpipe writes
-- observed from load metadata, not inferred from SQL. The source view is one
-- row PER FILE LOADED; aggregating per table-day bounds volume on busy
-- Snowpipe accounts and drops file names / stage paths (which can embed
-- sensitive path content) without needing a redaction pass.
SELECT
    table_catalog_name AS table_catalog,
    table_schema_name AS table_schema,
    table_name AS table_name,
    CAST(last_load_time AS DATE) AS load_date,
    IFF(pipe_name IS NULL, 'copy_into', 'snowpipe') AS load_method,
    count(*) AS n_files,
    sum(row_count) AS rows_loaded,
    sum(file_size) AS bytes_loaded,
    count_if(coalesce(error_count, 0) > 0) AS n_files_with_errors,
    min(last_load_time) AS first_load_time,
    max(last_load_time) AS last_load_time
FROM snowflake.account_usage.copy_history
WHERE last_load_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
{scope_filter}
GROUP BY 1, 2, 3, 4, 5
