"""MotherDuck migration assessment: inventory a source warehouse into DuckDB.

Source-specific knowledge lives in :mod:`md_migration_assessment.sources`;
Snowflake is the first (and currently only) adapter.
"""

__version__ = "0.1.0"

# Version of the meta.* table shapes. (raw.* shape versions are per source
# adapter — see each adapter's manifest.)
# v2: 'interrupted' extract-run status (Ctrl+C leaves honest coverage rows;
#   --resume re-runs them); meta.gaps includes it; unused meta.checkpoints
#   dropped (decision 16 cut intra-extract chunk checkpointing).
# v3 (source adapters): meta.collections gains source_kind and replaces the
#   snowflake_account/version/region/edition columns with source-neutral
#   source_deployment/version/region/edition. Pre-v3 files must be
#   re-collected (explicit migrations are not provided pre-1.0).
META_SCHEMA_VERSION = 3
