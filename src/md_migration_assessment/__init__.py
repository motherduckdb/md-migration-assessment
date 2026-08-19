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
# v8 (M3b review rounds 2-3): pipe_usage_history += source_kind — named
#   rows classified against ACCOUNT_USAGE.PIPES; unmatched rows are
#   'unclassified' (Iceberg automated refresh, an aged-out pipe, or a pipe
#   hidden from the collecting role), never presumed Snowpipe.
# v9 (M3c): server-side workload aggregates over QUERY_HISTORY — the GROUP
#   BY runs inside Snowflake, nothing per-query or textual ever lands
#   (spec decision 16): query_concurrency, query_tag_fingerprints,
#   client_app_fingerprints, query_shapes, query_workload_rollup,
#   query_dialect_constructs.
# v10 (M3c review rounds 1-2): query_concurrency rebuilt on exact event
#   timestamps with carriers bracketing the exact observation window
#   [window start, now - 45min QUERY_HISTORY latency watermark] — columns
#   become peak_concurrent_queries / avg_concurrent_queries / busy_seconds
#   (active_event_minutes dropped); the latency gap emits no rows and is
#   disclosed via actual_window_end; query_shapes exempts the '(unhashed)'
#   bucket from the top-N cap.
RAW_SCHEMA_VERSION = 10

# Version of the meta.* table shapes.
META_SCHEMA_VERSION = 1
