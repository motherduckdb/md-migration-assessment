# Databricks assessment Flight (test)

Runs `md-assess collect --source databricks` on MotherDuck Flights, then
`md-assess publish` to build the redacted database and its Dive. Adapted from the
Snowflake Flight recipe.

## Files

- `flight.py`: the Flight entrypoint.
- `requirements.txt`: the collector's dependencies. The collector itself is not here.
- `upload_wheel.sh`: builds the collector wheel and inserts it into
  `md_assess_dist.main.wheels` in your MotherDuck org. It prompts for the token
  without echoing it.

The Databricks adapter has no public release yet, so the Flight reads the newest
wheel from that table, checks its sha256, and unpacks it. Once a release exists,
pin it in `requirements.txt` and drop `load_collector()`.

## Setup

1. Databricks: create a service principal and an OAuth secret. Give it Can use
   on the SQL warehouse, then run the catalog and `system` schema grants in
   the Cookbook. The `system` grants need a metastore admin. For job code
   (the `job_tasks` extract, from the Jobs API) give it CAN VIEW on the jobs,
   or make it a workspace admin. Otherwise it sees only jobs it owns, and the
   counts are minimums.
2. MotherDuck: create a Flights secret named `databricks_creds` that holds
   `DATABRICKS_CLIENT_ID` and `DATABRICKS_CLIENT_SECRET`.
3. Run `./upload_wheel.sh`.
4. Create the Flight from `flight.py` and `requirements.txt` with this config:

| Key | Example |
|---|---|
| `DATABRICKS_HOST` | `https://<workspace>.cloud.databricks.com` |
| `DATABRICKS_WAREHOUSE_ID` | warehouse id |
| `TARGET_DB` | `md_assessment_databricks_flight` |
| `DIVE_TITLE` | `Databricks Migration Assessment (Flight test)` |
| `PROFILE` / `HISTORY_DAYS` / `REPLACE` | `standard` / `30` / `true` |

Optional settings are `SCOPE`, `DATABRICKS_WORKSPACES`, `SECRET_NAME`, `WHEEL_DB` and `WHEEL_VERSION`.

Tested on 2026-11-06 against a synthetic Unity Catalog workspace: 34 of 35
extractors completed. `hive_metastore_tables` is unavailable because that
workspace has Hive Metastore turned off.
