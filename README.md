# md-migration-assessment

Assess what a Snowflake → MotherDuck migration entails.

`md-assess` is a Python CLI you run against your own Snowflake account. It inventories
the deployment (catalog, features, workload, spend) into a local DuckDB database and
builds factual summaries: object inventory, sizing, workload profile, feature usage
counts, and collection coverage, with provenance.

**Status: pre-release (M3b). Feature inventory is complete; workload facts
come from aggregate histories. Not yet released for customer use.**

## Trust model

- **Local/private mode (default):** the collector makes zero network calls except to
  Snowflake. No telemetry, no MotherDuck connection. Output is a single local
  `.duckdb` file (mode `0600`) you can inspect with any DuckDB client.
- Uploading anything to MotherDuck is a separate, explicit command that builds a
  sanitized handoff database.

## Quickstart (local mode)

```bash
export SNOWFLAKE_ACCOUNT=...
export SNOWFLAKE_USER=...
export SNOWFLAKE_PASSWORD=...        # or SNOWFLAKE_PRIVATE_KEY_PATH
export SNOWFLAKE_WAREHOUSE=...
export SNOWFLAKE_ROLE=...            # optional

md-assess collect --profile standard --output assessment.duckdb
md-assess report  --db assessment.duckdb
```

Profiles: `lite` (INFORMATION_SCHEMA only, any role), `standard` (requires
ACCOUNT_USAGE access), `full` (adds the workload story: warehouse metering and
load, daily metering by service, copy/pipe/task history, a login-derived
client/driver inventory, and server-side aggregates over QUERY_HISTORY —
concurrency peaks, tool fingerprints, anonymous query shapes, type/spill/bytes
rollups, and dialect-construct counts). Per-query rows and workload query text
are **never** collected by any profile: the QUERY_HISTORY extracts run their
GROUP BY inside Snowflake, so only counts, opaque hashes, and derived labels
land — output size scales with catalog, warehouses, and wall-clock time, not
query volume. The aggregate scans run on your warehouse (an X-Small suffices;
compute cost scales with your own history volume). `--history-days`
(default 30, max 365) sets the workload extracts' lookback window.
A partial collection is always a valid output: every extractor records its coverage
in `meta.extract_runs`, and missing evidence is never presented as an observed zero.

**Interrupting and resuming.** `collect` prints per-extractor progress to stderr
and is safe to stop with Ctrl+C at any point: the in-flight extractor and every
unattempted one are recorded with status `interrupted` (visible in `meta.gaps`),
the report layer is built over whatever landed, and the database is a valid
partial collection. Continue it with:

```bash
md-assess collect --output assessment.duckdb --resume
```

Resume skips extractors already `complete`, re-runs everything else (including
previously `failed`/`unavailable` ones — useful after fixing grants), and takes
profile/scope/window from the existing collection rather than from flags. It
refuses to mix accounts: resuming against a different Snowflake account than
the one recorded in `meta.collections` is an error.

## Privileges

The simple path is `GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <role>`.
A least-privilege matrix (per-extractor Snowflake database roles, edition
requirements, and INFORMATION_SCHEMA fallbacks) is at the bottom of this file.

## License

Apache-2.0. Portions of the extraction SQL are derived from
[google/dwh-migration-tools](https://github.com/google/dwh-migration-tools)
(Apache-2.0); files retain attribution headers.


## Least-privilege matrix

The one-line grant is `GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <role>`.
To grant less, use Snowflake's database roles on the `SNOWFLAKE` database — the
matrix below lists what each extractor needs. Extractors whose grants are
withheld degrade to `unavailable` rows in `meta.extract_runs` naming the
missing privilege; the collection stays valid. SHOW-command extracts run with
any role and report the objects that role can see.

| Extractor | Profile | Source | Minimal privilege | Min edition |
|---|---|---|---|---|
| databases | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| schemata | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| tables | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| columns | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| views | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| functions | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| procedures | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| table_storage_metrics | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| stage_storage_usage_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| database_storage_usage_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| masking_policies | standard | ACCOUNT_USAGE | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise |
| row_access_policies | standard | ACCOUNT_USAGE | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise |
| policy_references | standard | ACCOUNT_USAGE | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise |
| tags | standard | ACCOUNT_USAGE | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise |
| tag_references | standard | ACCOUNT_USAGE | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise |
| pipes | standard | ACCOUNT_USAGE | SNOWFLAKE.OBJECT_VIEWER | Standard |
| tasks | standard | ACCOUNT_USAGE | SNOWFLAKE.OBJECT_VIEWER | Standard |
| stages | standard | ACCOUNT_USAGE | SNOWFLAKE.OBJECT_VIEWER | Standard |
| listings | standard | ACCOUNT_USAGE | SNOWFLAKE.SECURITY_VIEWER | Standard |
| shares | standard | ACCOUNT_USAGE | SNOWFLAKE.SECURITY_VIEWER | Standard |
| external_tables | lite | INFORMATION_SCHEMA | any role (objects visible to the role) | Standard |
| cortex_ai_functions_usage_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| search_optimization_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Enterprise |
| snowpipe_streaming_client_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| streams | standard | SHOW | any role (objects visible to the role) | Standard |
| warehouses | standard | SHOW | any role (warehouses visible to the role) | Standard |
| streamlit_apps | standard | SHOW | any role (objects visible to the role) | Standard |
| notebooks | standard | SHOW | any role (objects visible to the role) | Standard |
| applications | standard | SHOW | any role (objects visible to the role) | Standard |
| application_packages | standard | SHOW | any role (objects visible to the role) | Standard |
| catalog_integrations | standard | SHOW | any role (integrations visible to the role) | Standard |
| show_shares | standard | SHOW | any role (shares visible to the role) | Standard |
| roles | standard | ACCOUNT_USAGE | SNOWFLAKE.SECURITY_VIEWER | Standard |
| warehouse_metering_history | full | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| warehouse_load_history | full | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| metering_daily_history | full | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| copy_history | full | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| pipe_usage_history | full | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER + SNOWFLAKE.OBJECT_VIEWER | Standard |
| task_history | full | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| login_history | full | ACCOUNT_USAGE | SNOWFLAKE.SECURITY_VIEWER | Standard |
| query_concurrency | full | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| query_tag_fingerprints | full | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| client_app_fingerprints | full | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER + SNOWFLAKE.SECURITY_VIEWER | Standard |
| query_shapes | full | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| query_workload_rollup | full | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| query_dialect_constructs | full | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |

The `lite` profile needs no ACCOUNT_USAGE access at all: any role sees its own
objects through per-database INFORMATION_SCHEMA walks.
