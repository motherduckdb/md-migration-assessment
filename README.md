# md-migration-assessment

Assess what a migration to MotherDuck entails.

`md-assess` is a Python CLI you run against your own data warehouse. It inventories
the deployment (catalog, features, workload, spend) into a local DuckDB database and
builds factual summaries: object inventory, sizing, workload profile, feature usage
counts, and collection coverage, with provenance.

Warehouse-specific knowledge lives in a *source adapter*; the collector, output
schema, privacy policy, and report contract are shared. **Snowflake is the
adapter that ships today.** See [Adding a source](#adding-a-source) for what a
second adapter involves.

**Status: Public Preview.** The Snowflake assessment is available to qualified
prospects and customers with support from MotherDuck. Interfaces and output
schemas may change before 1.0; use the latest pinned release and re-collect when
a release notes a schema change. See [Support](SUPPORT.md) and
[Data handling](docs/DATA_HANDLING.md) before running or sharing an assessment.

## Supported sources

| `--source` | Status | Install extra | Connection |
|---|---|---|---|
| `snowflake` (default) | complete assessment (catalog, sizing, features, workload aggregates) | `md-migration-assessment[snowflake]` | `SNOWFLAKE_*` env vars, see below |

## Trust model

- **Local/private mode (default):** the collector connects to the source warehouse
  and does not implement telemetry, a MotherDuck connection, or automatic upload.
  Output is a single local `.duckdb` file (mode `0600`) you can inspect with any
  DuckDB client.
- The separate `handoff` command builds a reduced database for manual sharing. It
  excludes query text and source bodies, but intentionally retains object names,
  user/role identities, comments, and tag values. Review its manifest and the
  [data-handling guide](docs/DATA_HANDLING.md) before sharing it.

## Quickstart: Snowflake (local mode)

Requires Python 3.10+. Fill in the `<...>` values, then the whole block runs
as-is. This installs the versioned `v0.1.2` GitHub release asset rather than
the moving `main` branch:

```bash
# 1. Install uv (skip if you already have uv, or see the pip variant below)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install the collector as a CLI tool, with the Snowflake client extra
uv tool install "md-migration-assessment[snowflake] @ https://github.com/motherduckdb/md-migration-assessment/releases/download/v0.1.2/md_migration_assessment-0.1.2-py3-none-any.whl"

# 3. Snowflake connection — external-browser SSO is the recommended default
export SNOWFLAKE_ACCOUNT="<orgname-accountname>"   # e.g. myorg-myaccount
export SNOWFLAKE_USER="<username>"
export SNOWFLAKE_AUTHENTICATOR="externalbrowser"
export SNOWFLAKE_WAREHOUSE="<any_small_warehouse>" # X-Small is enough
export SNOWFLAKE_ROLE="<role>"                     # optional; omit for default

# 4. Collect into a local DuckDB file and print the summary
md-assess collect --source snowflake --profile standard --output assessment.duckdb
md-assess report  --db assessment.duckdb
```

`--source snowflake` is the default and may be omitted.

For non-interactive use, key-pair authentication is preferred: unset
`SNOWFLAKE_AUTHENTICATOR`, set `SNOWFLAKE_PRIVATE_KEY_PATH`, and optionally set
`SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` for an encrypted key. Password authentication
is also supported through `SNOWFLAKE_PASSWORD`, but avoid placing passwords in
shell history or checked-in environment files.

Without uv, any of these work in its place:

```bash
pipx install "md-migration-assessment[snowflake] @ https://github.com/motherduckdb/md-migration-assessment/releases/download/v0.1.2/md_migration_assessment-0.1.2-py3-none-any.whl"
# or, into an existing virtualenv:
pip install "md-migration-assessment[snowflake] @ https://github.com/motherduckdb/md-migration-assessment/releases/download/v0.1.2/md_migration_assessment-0.1.2-py3-none-any.whl"
# or, hacking on the repo itself:
git clone https://github.com/motherduckdb/md-migration-assessment.git
cd md-migration-assessment && uv run --extra snowflake md-assess --help
```

The output is a single local `assessment.duckdb` (mode `0600`) you can open
with any DuckDB client — the interesting tables are `report.*` (facts),
`raw.*` (evidence), and `meta.extract_runs` (per-extractor coverage).
`meta.collections.source_kind` records which adapter produced the file, and
the `assess`/`handoff` commands resolve the same adapter from it. Collection
makes no network calls except to Snowflake.

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
refuses to mix deployments: resuming against a different Snowflake account
(or a different source kind) than the one recorded in `meta.collections` is
an error.

## Snowflake privileges

Two ways to grant what the collector reads: the recommended tiered database
roles below, or a single `IMPORTED PRIVILEGES` grant as a quick path. A
least-privilege matrix (per-extractor Snowflake database roles, edition
requirements, and INFORMATION_SCHEMA fallbacks) is at the bottom of this file.

### Recommended minimum grant

Use Snowflake's built-in database roles on the `SNOWFLAKE` database so the
grant matches exactly what the collector reads. In practice there are three
meaningful tiers:

```sql
-- Useful floor: account-wide catalog inventory + storage and spend history
-- from ACCOUNT_USAGE. Sizes the estate and the bill, but says nothing
-- about what actually runs.
GRANT DATABASE ROLE SNOWFLAKE.OBJECT_VIEWER TO ROLE md_assess;
GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER  TO ROLE md_assess;

-- Decision grade (recommended): adds the QUERY_HISTORY aggregates
-- (concurrency, query shapes, dialect constructs), ACCESS_HISTORY read
-- heat, and the masking / row-access-policy inventory — the evidence a
-- migration decision actually turns on.
GRANT DATABASE ROLE SNOWFLAKE.GOVERNANCE_VIEWER TO ROLE md_assess;

-- Optional: login history, client-app fingerprints (SESSIONS join),
-- roles, grant summaries, shares. Peripheral to the assessment — the
-- right tier to concede if the security team objects.
GRANT DATABASE ROLE SNOWFLAKE.SECURITY_VIEWER TO ROLE md_assess;
```

Any tier is safe to run: extracts whose grants are missing land as
`unavailable` coverage rows (never silent zeros), and `--resume` re-runs
exactly those extracts after grants are widened — no recollection needed.
Without any of these roles the collector still works in `--profile lite`
(INFORMATION_SCHEMA + SHOW), but coverage is limited to objects the role
happens to have privileges on. Note `table_read_heat` also requires
Enterprise edition regardless of role.

### Quick path: one grant

If a granular grant is impractical for the evaluation, one statement covers
every ACCOUNT_USAGE extractor:

```sql
GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE md_assess;
```

This is equivalent to all of the built-in viewer roles above at once, plus the
`SNOWFLAKE` database's organization-usage, reader-account, and data-sharing
views, which the tool never reads. It grants no access to any of your own
databases or tables. Choose it to get started quickly; choose the tiered roles
above when your security team wants the grant to match exactly what is read.

### Role visibility: what no grant on `SNOWFLAKE` covers

Neither path affects the SHOW-based inventories (warehouses, streams, dynamic
tables, integrations, alerts, and the other rows marked `SHOW` in the matrix
below) or the INFORMATION_SCHEMA walks used by `lite`. Those list only objects
the collecting role has some privilege on, and Snowflake gives no way to detect
what the role cannot see. So for those extracts a `complete` status means
"complete for what this role can see": counts are lower bounds, and a zero may
mean missing grants rather than absence. The collector says so in
`meta.extract_runs.error_detail`, and every affected `report.feature_inventory`
row carries a note.

To make those inventories account-wide, run the collection as a role with broad
object visibility — for warehouses that means `MONITOR` or `USAGE` on each
warehouse (or `MANAGE WAREHOUSES`), and for schema-level objects `USAGE` on the
databases and schemas that hold them. A role such as `SYSADMIN` typically has
this already; a purpose-built `md_assess` role usually does not.

## License

Apache-2.0. Portions of the extraction SQL are derived from
[google/dwh-migration-tools](https://github.com/google/dwh-migration-tools)
(Apache-2.0); files retain attribution headers.


## Adding a source

The repository is structured so a second warehouse is an additive package,
not a rewrite. What is shared and what is per-source:

| Shared (source-neutral) | Per-adapter (`src/md_migration_assessment/sources/<kind>/`) |
|---|---|
| runner: profiles, scope, ingestion, coverage rows, Ctrl+C / `--resume` semantics | connection and credentials (`open()`) |
| `meta.*` schema and `meta.extract_runs` status contract | extractor manifest: names, privacy classes, acquisition strategies, SQL resources, `raw_schema_version` |
| privacy classes and the fail-closed handoff column policy | error classification (`classify_error`: privilege gap vs. real failure) |
| `report.feature_inventory` shape and observation-status contract | scope grammar (identifier syntax, case folding, quoting) |
| CLI | feature signals and planned signals |
| | report fact builders (`report.sizing`, workload facts, ...) |

An adapter implements the `SourceAdapter` and `Connection` protocols in
`src/md_migration_assessment/sources/base.py` and registers under a name in
`sources/__init__.py`; its client library goes in a same-named optional extra
in `pyproject.toml`. Extractors declare an ordered tuple of strategies from
`collect/extractor.py`: a deployment-wide `GlobalQuery` (optionally gated
behind `standard`), a `PerDatabaseQuery` walk that records partial coverage,
or a client-materialized `Command` with an explicit column allowlist. The
runner tries them in order, falling through on `unavailable` and stopping
on a real failure.

`tests/fake_adapter.py` is a complete synthetic adapter with no warehouse
client; `tests/test_adapter_seam.py` runs collection, resume, report, and
handoff against it and is the template for a new adapter's tests. The
Snowflake adapter's own tests live in `tests/snowflake/`.

`report.feature_inventory` is the only cross-source report relation today;
the other `report.*` tables are adapter-owned until a second source shows
which columns are genuinely common.

## Snowflake least-privilege matrix

The quick path is `GRANT IMPORTED PRIVILEGES ON DATABASE SNOWFLAKE TO ROLE <role>`.
To grant less, start from the tiered recommendation in
[Snowflake privileges](#snowflake-privileges) above; the matrix below lists what each individual
extractor needs. Extractors whose grants are
withheld degrade to `unavailable` rows in `meta.extract_runs` naming the
missing privilege; the collection stays valid. SHOW-command extracts run with
any role and report only the objects that role can see (see
[Role visibility](#role-visibility-what-no-grant-on-snowflake-covers) above).

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
