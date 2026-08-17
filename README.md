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
requirements, and INFORMATION_SCHEMA fallbacks) will be documented here.

## License

Apache-2.0. Portions of the extraction SQL are derived from
[google/dwh-migration-tools](https://github.com/google/dwh-migration-tools)
(Apache-2.0); files retain attribution headers.
