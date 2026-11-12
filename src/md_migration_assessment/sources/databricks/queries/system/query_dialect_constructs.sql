-- md-assess-extract: query_dialect_constructs
-- Counts of Databricks SQL constructs that need a rewrite (or a check) on
-- DuckDB, per day, evaluated entirely inside Databricks: statement_text
-- appears only inside count_if predicates, so the text never leaves the
-- warehouse. Patterns are heuristics (a construct inside a string literal
-- can match); they size the rewrite list, they are not parse results. The
-- colon-path pattern needs an identifier character before ':' and a letter
-- after, which excludes '::' casts and clock times.
SELECT
    CAST(start_time AS DATE) AS usage_date,
    count(*) AS n_queries_scanned,
    count_if(regexp_like(statement_text, r'(?i)\blateral\s+view\b')) AS n_lateral_view,
    count_if(regexp_like(statement_text, r'(?i)\b[a-z_][a-z0-9_]*\s*->')) AS n_lambda,
    count_if(regexp_like(statement_text, r'[A-Za-z0-9_`\]]:[A-Za-z_`$]')) AS n_variant_path,
    count_if(regexp_like(statement_text, r'(?i)\b(version|timestamp)\s+as\s+of\b')) AS n_time_travel,
    count_if(regexp_like(statement_text, r'(?i)\bmerge\s+into\b')) AS n_merge,
    count_if(regexp_like(statement_text, r'(?i)\b(un)?pivot\s*\(')) AS n_pivot_unpivot,
    count_if(regexp_like(statement_text, r'(?i)\b(read_files|cloud_files|read_kafka|read_kinesis)\s*\(')) AS n_file_stream_sources,
    count_if(regexp_like(statement_text, r'(?i)\b(apply\s+changes|auto\s+cdc)\s+into\b')) AS n_apply_changes,
    count_if(regexp_like(statement_text, r'(?i)\btable_changes\s*\(')) AS n_change_feed_reads,
    count_if(regexp_like(statement_text, r'(?i)\bai_[a-z_]+\s*\(')) AS n_ai_functions,
    count_if(regexp_like(statement_text, r'(?i)\b(from_json|to_json|get_json_object|json_tuple|schema_of_json)\s*\(')) AS n_json_functions,
    count_if(regexp_like(statement_text, r'(?i)\bidentifier\s*\(')) AS n_identifier_fn,
    count_if(statement_type IN ('OPTIMIZE', 'VACUUM')) AS n_delta_maintenance
FROM system.query.history
WHERE start_time >= current_timestamp() - INTERVAL {window_days} DAYS
{workspace_filter}
AND statement_text IS NOT NULL
GROUP BY 1
