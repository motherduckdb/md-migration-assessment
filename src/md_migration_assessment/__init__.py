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
RAW_SCHEMA_VERSION = 5

# Version of the meta.* table shapes.
META_SCHEMA_VERSION = 1
