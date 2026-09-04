-- md-assess-extract: query_dialect_constructs
-- Dialect-sensitive construct counts over workload SQL, evaluated ENTIRELY
-- inside Snowflake (spec decision 16): QUERY_TEXT appears only inside
-- aggregate predicates, so the text itself never crosses the wire and never
-- exists in collector memory — only per-day match counts land. Patterns are
-- heuristics (a construct inside a string literal can false-positive);
-- counts are workload-frequency signals, not parse results. The colon-path
-- pattern requires an identifier character before ':' and a letter after,
-- which excludes '::' casts.
SELECT
    CAST(start_time AS DATE) AS usage_date,
    count(*) AS n_queries_scanned,
    count_if(REGEXP_LIKE(query_text, '.*\\bFLATTEN\\s*\\(.*', 'is')) AS n_flatten,
    count_if(REGEXP_LIKE(query_text, '.*[A-Za-z0-9_"\\]]:[A-Za-z_"].*', 'is')) AS n_colon_path,
    count_if(REGEXP_LIKE(query_text, '.*\\b(PIVOT|UNPIVOT)\\s*\\(.*', 'is')) AS n_pivot_unpivot,
    count_if(REGEXP_LIKE(query_text, '.*\\bCONNECT\\s+BY\\b.*', 'is')) AS n_connect_by,
    count_if(REGEXP_LIKE(query_text, '.*\\bMATCH_RECOGNIZE\\b.*', 'is')) AS n_match_recognize,
    count_if(REGEXP_LIKE(query_text, '.*\\b(AT|BEFORE)\\s*\\(\\s*(OFFSET|TIMESTAMP|STATEMENT)\\s*=>.*', 'is')) AS n_time_travel,
    count_if(REGEXP_LIKE(query_text, '.*\\bRESULT_SCAN\\s*\\(.*', 'is')) AS n_result_scan,
    count_if(REGEXP_LIKE(query_text, '.*\\bIDENTIFIER\\s*\\(.*', 'is')) AS n_identifier_fn
FROM snowflake.account_usage.query_history
WHERE start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
AND COALESCE(is_client_generated_statement, FALSE) = FALSE
{scope_filter}
GROUP BY 1
