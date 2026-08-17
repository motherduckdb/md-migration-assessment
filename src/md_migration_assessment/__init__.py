"""Snowflake -> MotherDuck migration assessment."""

__version__ = "0.1.0.dev0"

# Version of the raw.* table shapes. Bump on any change to an extract's column
# set; schema migrations must be explicit (spec §3).
RAW_SCHEMA_VERSION = 1

# Version of the meta.* table shapes.
META_SCHEMA_VERSION = 1
