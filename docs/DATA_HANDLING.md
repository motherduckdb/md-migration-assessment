# Data handling

Read this guide before running an assessment or sharing its output.

## Network and upload behavior

The collector connects to the configured source warehouse. It does not implement
telemetry, a MotherDuck connection, or automatic upload. Collection output stays
in the local path supplied with `--output` and is created with mode `0600`.

There is no upload command in the Public Preview. Any transfer to MotherDuck is a
separate, manual action performed by the customer or prospect after reviewing the
handoff contents.

## What the files contain

| Data category | Local assessment | Default handoff |
|---|---:|---:|
| Workload query text | Never collected | Not present |
| View, function, procedure, policy, task, and alert source bodies | Included when visible to the Snowflake role | Excluded |
| Database, schema, table, column, integration, and warehouse names | Included | Included |
| User names, role names, and ownership fields | Included where required by an extractor | Included |
| Object comments and tag values | Included | Included |
| Aggregate workload, storage, usage, and coverage facts | Included | Included |

The local assessment is private source evidence and should not be shared. The
handoff is reduced, not anonymous: object names, identities, comments, and tag
values may reveal confidential information or personal data.

## Building and reviewing a handoff

Build a separate handoff file; the command refuses to overwrite an existing one:

```bash
md-assess handoff --db assessment.duckdb --dest handoff.duckdb
```

The command prints a manifest listing every table, excluded column, disclosed
sensitive class, and unclassified included column. Review that output before
sharing the file.

Use a copy for any additional sanitization:

```bash
cp handoff.duckdb handoff-for-sharing.duckdb
duckdb handoff-for-sharing.duckdb
```

Inside DuckDB, inventory the remaining columns:

```sql
SELECT table_schema, table_name, column_name
FROM information_schema.columns
WHERE table_schema IN ('raw', 'report')
ORDER BY table_schema, table_name, ordinal_position;
```

For a more conservative handoff, remove the raw evidence entirely and then
review the remaining `report.*` tables for object-name or sample columns:

```sql
DROP SCHEMA raw CASCADE;
```

Use `ALTER TABLE ... DROP COLUMN ...` or create a new table with an explicit
column list to remove information the customer does not approve for sharing.
Inspect representative values, not only column names: comments, tags, and object
names can themselves contain sensitive content.

The customer or prospect controls the transfer method, recipients, retention,
and deletion schedule. Do not email either database or place it in a public or
unapproved file-sharing location.
