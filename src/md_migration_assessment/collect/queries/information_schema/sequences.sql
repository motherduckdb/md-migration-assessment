-- Sequence inventory (M3d, decision 18): DuckDB has CREATE SEQUENCE, so the
-- mapping is near 1:1 — the inventory is the porting worklist.
SELECT
    sequence_catalog,
    sequence_schema,
    sequence_name,
    data_type,
    start_value,
    -- INCREMENT is a Snowflake reserved word, even as an alias (found live)
    "INCREMENT" AS increment_by,
    comment
FROM {database}.information_schema.sequences
WHERE sequence_schema <> 'INFORMATION_SCHEMA'
{scope_filter}
