# md-migration-assessment

Assess what a Snowflake → MotherDuck migration entails.

`md-assess` is a Python CLI you run against your own Snowflake account. It inventories
the deployment (catalog, features, workload, spend) into a local DuckDB database and
builds factual summaries: object inventory, sizing, workload profile, feature usage
counts, and collection coverage, with provenance.

**Status: pre-release (M3 complete). Feature inventory and workload facts are
in; delivery (upload, Dive, Flight) is next. Not yet released for customer use.**

## Trust model

- **Local/private mode (default):** the collector makes zero network calls except to
  Snowflake. No telemetry, no MotherDuck connection. Output is a single local
  `.duckdb` file (mode `0600`) you can inspect with any DuckDB client.
- Uploading anything to MotherDuck is a separate, explicit command that builds a
  sanitized handoff database.

## Quickstart (local mode)

Requires Python 3.10+ and read access to this repository (git authenticates
with your normal GitHub credentials). Fill in the three `<...>` values, then
the whole block runs as-is:

```bash
# 1. Install uv (skip if you already have uv, or see the pip variant below)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install the collector as a CLI tool
uv tool install git+https://github.com/motherduckdb/md-migration-assessment.git

# 3. Snowflake connection — env vars only, nothing is written to disk
export SNOWFLAKE_ACCOUNT="<orgname-accountname>"   # e.g. myorg-myaccount
export SNOWFLAKE_USER="<username>"
export SNOWFLAKE_PASSWORD="<password>"             # or key pair: see below
export SNOWFLAKE_WAREHOUSE="<any_small_warehouse>" # X-Small is enough
export SNOWFLAKE_ROLE="<role>"                     # optional; omit for default

# 4. Collect into a local DuckDB file and print the summary
md-assess collect --profile standard --output assessment.duckdb
md-assess report  --db assessment.duckdb
```

Key-pair auth instead of a password: set `SNOWFLAKE_PRIVATE_KEY_PATH` (and
`SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` if the key is encrypted) and skip
`SNOWFLAKE_PASSWORD`. External browser SSO: set
`SNOWFLAKE_AUTHENTICATOR=externalbrowser` and skip the password.

Without uv, any of these work in its place:

```bash
pipx install git+https://github.com/motherduckdb/md-migration-assessment.git
# or, into an existing virtualenv:
pip install git+https://github.com/motherduckdb/md-migration-assessment.git
# or, hacking on the repo itself:
git clone https://github.com/motherduckdb/md-migration-assessment.git
cd md-migration-assessment && uv run md-assess --help
```

The output is a single local `assessment.duckdb` (mode `0600`) you can open
with any DuckDB client — the interesting tables are `report.*` (facts),
`raw.*` (evidence), and `meta.extract_runs` (per-extractor coverage).
Collection makes no network calls except to Snowflake.

Two profiles: `lite` (INFORMATION_SCHEMA and SHOW only — any role, no
ACCOUNT_USAGE access needed) and `standard` (the complete assessment:
catalog, sizing, the full features taxonomy, and the workload story —
warehouse metering and load, daily metering by service, copy/pipe/task
history, a login-derived client/driver inventory, and server-side aggregates
over QUERY_HISTORY: concurrency peaks, tool fingerprints, anonymous query
shapes, type/spill/bytes rollups, and dialect-construct counts). Per-query
rows and workload query text are **never** collected by any profile: the
QUERY_HISTORY extracts run their GROUP BY inside Snowflake, so only counts,
opaque hashes, and derived labels land — output size scales with catalog,
warehouses, and wall-clock time, not query volume. The aggregate scans run on
your warehouse (an X-Small suffices; compute cost scales with your own
history volume). `--history-days` (default 30, max 365) sets the workload
extracts' lookback window.
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
| warehouse_metering_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| warehouse_load_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| metering_daily_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| copy_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| pipe_usage_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER + SNOWFLAKE.OBJECT_VIEWER | Standard |
| task_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| login_history | standard | ACCOUNT_USAGE | SNOWFLAKE.SECURITY_VIEWER | Standard |
| query_concurrency | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| query_tag_fingerprints | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| client_app_fingerprints | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER + SNOWFLAKE.SECURITY_VIEWER | Standard |
| query_shapes | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| query_workload_rollup | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| query_dialect_constructs | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Standard |
| object_dependencies | standard | ACCOUNT_USAGE | SNOWFLAKE.OBJECT_VIEWER | Standard |
| table_read_heat | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise |
| table_constraints | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| referential_constraints | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| sequences | lite | INFORMATION_SCHEMA | any role (objects visible to the role) | Standard |
| file_formats | lite | ACCOUNT_USAGE + fallback | SNOWFLAKE.OBJECT_VIEWER | Standard |
| grants_to_roles_summary | standard | ACCOUNT_USAGE (server-side aggregate) | SNOWFLAKE.SECURITY_VIEWER | Standard |
| dynamic_table_refresh_history | standard | ACCOUNT_USAGE | SNOWFLAKE.USAGE_VIEWER | Standard |
| account_parameters | standard | SHOW | any role | Standard |
| network_policies | standard | SHOW | any role (policies visible to the role) | Standard |
| storage_integrations | standard | SHOW | any role (integrations visible to the role) | Standard |
| notification_integrations | standard | SHOW | any role (integrations visible to the role) | Standard |
| api_integrations | standard | SHOW | any role (integrations visible to the role) | Standard |
| external_access_integrations | standard | SHOW | any role (integrations visible to the role) | Standard |
| external_volumes | standard | SHOW | any role (volumes visible to the role) | Standard |
| dynamic_tables | standard | SHOW | any role (objects visible to the role) | Standard |
| alerts | standard | SHOW | any role (objects visible to the role) | Standard |
| event_tables | standard | SHOW | any role (objects visible to the role) | Standard |
| replication_groups | standard | SHOW | any role (groups visible to the role) | Standard |
| failover_groups | standard | SHOW | any role (groups visible to the role) | Business Critical |
| resource_monitors | standard | SHOW | any role (monitors visible to the role) | Standard |

The `lite` profile needs no ACCOUNT_USAGE access at all: any role sees its own
objects through per-database INFORMATION_SCHEMA walks.
