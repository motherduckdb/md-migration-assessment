"""Snowflake -> MotherDuck migration assessment."""

__version__ = "0.1.0.dev0"

# Version of the raw.* table shapes. Bump on any change to an extract's column
# set; schema migrations must be explicit (spec §3).
# v2 (M2): tables += is_iceberg/is_dynamic/is_hybrid; functions += packages/
#   runtime_version; new feature extracts (policies, tags, pipes, roles).
# v3 (M2 review): new feature extracts (tasks, stages, listings).
# v4 (M2 review follow-up): new shares extract.
# v5 (M3a): external_tables, usage-history feature extracts, SHOW-based
#   extracts (streams, warehouses, streamlits, notebooks, applications,
#   application packages, catalog integrations, shares listing).
# v6 (M3b): aggregate workload extracts (warehouse metering/load, daily
#   metering, copy/pipe/task history, login-derived driver inventory).
#   Per-query QUERY_HISTORY is deliberately absent (spec decision 15).
# v7 (M3b review): copy_history += load_status outcome rows (failed COPY
#   attempts are evidence, not writes); pipe_usage_history += pipe_id and
#   name-derived residency columns, hidden auto-refresh rows excluded.
# v8 (M3b review round 2): pipe_usage_history += source_kind — named rows
#   classified against ACCOUNT_USAGE.PIPES; unmatched rows are
#   'unclassified_refresh' (Iceberg automated refresh), never presumed
#   Snowpipe.
RAW_SCHEMA_VERSION = 8

# Version of the meta.* table shapes.
META_SCHEMA_VERSION = 1
