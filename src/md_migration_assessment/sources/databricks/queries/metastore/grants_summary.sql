-- md-assess-extract: grants_summary
-- Grant counts per (securable type, privilege) from the metastore-wide
-- privilege views. Grantees land only as a distinct count. Lists only the
-- grants the collecting principal can see.
SELECT 'CATALOG' AS securable_type, privilege_type, count(*) AS n_grants,
       count(DISTINCT grantee) AS n_grantees
FROM system.information_schema.catalog_privileges GROUP BY 1, 2
UNION ALL
SELECT 'SCHEMA', privilege_type, count(*), count(DISTINCT grantee)
FROM system.information_schema.schema_privileges GROUP BY 1, 2
UNION ALL
SELECT 'TABLE', privilege_type, count(*), count(DISTINCT grantee)
FROM system.information_schema.table_privileges GROUP BY 1, 2
UNION ALL
SELECT 'ROUTINE', privilege_type, count(*), count(DISTINCT grantee)
FROM system.information_schema.routine_privileges GROUP BY 1, 2
UNION ALL
SELECT 'VOLUME', privilege_type, count(*), count(DISTINCT grantee)
FROM system.information_schema.volume_privileges GROUP BY 1, 2
