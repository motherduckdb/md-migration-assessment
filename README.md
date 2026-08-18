# md-migration-assessment

Assess what a Snowflake → MotherDuck migration entails.

`md-assess` is a Python CLI you run against your own Snowflake account. It inventories
the deployment (catalog, features, workload, spend) into a local DuckDB database and
builds factual summaries: object inventory, sizing, workload profile, feature usage
counts, and collection coverage — observations only, with provenance.

The scored interpretation (feature-by-feature MotherDuck compatibility, effort tiers,
cost scenarios) is applied and walked through with you by a MotherDuck engineer — those
judgments change with every MotherDuck release, so they're kept where someone can keep
them current and qualify them, rather than baked into this repo.

**Status: pre-release scaffolding (M1). Not yet usable for real assessments.**

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
ACCOUNT_USAGE access), `full` (adds query history and metering time series).
A partial collection is always a valid output: every extractor records its coverage
in `meta.extract_runs`, and missing evidence is never presented as an observed zero.

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

The `lite` profile needs no ACCOUNT_USAGE access at all: any role sees its own
objects through per-database INFORMATION_SCHEMA walks.
