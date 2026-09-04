-- md-assess-extract: table_read_heat
-- Table read heat, aggregated INSIDE Snowflake per accessed object and day
-- (M3d, decision 18 — the narrow read-heat exception to decision 16; write-
-- method inference stays cut). The authoritative hot-vs-cold scoping input:
-- BASE_OBJECTS_ACCESSED names the physical objects a query actually read.
-- User identities land only as a distinct count, never as names. Rows scale
-- with catalog x window days, never query volume. Enterprise edition,
-- GOVERNANCE_VIEWER. ACCOUNT_USAGE latency (~3h) makes recent counts lower
-- bounds — like every windowed extract, never fabricated zeros.
SELECT
    split_part(o.value:"objectName"::VARCHAR, '.', 1) AS object_database,
    split_part(o.value:"objectName"::VARCHAR, '.', 2) AS object_schema,
    o.value:"objectName"::VARCHAR AS object_name,
    o.value:"objectDomain"::VARCHAR AS object_domain,
    CAST(ah.query_start_time AS DATE) AS read_date,
    count(*) AS n_reads,
    count(DISTINCT ah.user_name) AS n_distinct_readers,
    min(ah.query_start_time) AS first_read,
    max(ah.query_start_time) AS last_read
FROM snowflake.account_usage.access_history ah,
LATERAL FLATTEN(input => ah.base_objects_accessed) o
WHERE ah.query_start_time >= DATEADD(day, -{window_days}, CURRENT_TIMESTAMP)
AND o.value:"objectName" IS NOT NULL
{scope_filter}
GROUP BY 1, 2, 3, 4, 5
