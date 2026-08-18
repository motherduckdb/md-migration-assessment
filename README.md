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
missing privilege; the collection stays valid.

| Extractor | Profile | Minimal privilege | Min edition | INFORMATION_SCHEMA fallback |
|---|---|---|---|---|
| databases | lite | SNOWFLAKE.OBJECT_VIEWER | Standard | yes |
| schemata | lite | SNOWFLAKE.OBJECT_VIEWER | Standard | yes |
| tables | lite | SNOWFLAKE.OBJECT_VIEWER | Standard | yes |
| columns | lite | SNOWFLAKE.OBJECT_VIEWER | Standard | yes |
| views | lite | SNOWFLAKE.OBJECT_VIEWER | Standard | yes |
| functions | lite | SNOWFLAKE.OBJECT_VIEWER | Standard | yes |
| procedures | lite | SNOWFLAKE.OBJECT_VIEWER | Standard | yes |
| table_storage_metrics | standard | SNOWFLAKE.OBJECT_VIEWER | Standard | no |
| stage_storage_usage_history | standard | SNOWFLAKE.USAGE_VIEWER | Standard | no |
| database_storage_usage_history | standard | SNOWFLAKE.USAGE_VIEWER | Standard | no |
| masking_policies | standard | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise | no |
| row_access_policies | standard | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise | no |
| policy_references | standard | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise | no |
| tags | standard | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise | no |
| tag_references | standard | SNOWFLAKE.GOVERNANCE_VIEWER | Enterprise | no |
| pipes | standard | SNOWFLAKE.OBJECT_VIEWER | Standard | no |
| tasks | standard | SNOWFLAKE.OBJECT_VIEWER | Standard | no |
| stages | standard | SNOWFLAKE.OBJECT_VIEWER | Standard | no |
| listings | standard | SNOWFLAKE.OBJECT_VIEWER | Standard | no |
| roles | standard | SNOWFLAKE.SECURITY_VIEWER | Standard | no |

The `lite` profile needs no ACCOUNT_USAGE access at all: any role sees its own
objects through per-database INFORMATION_SCHEMA walks.
