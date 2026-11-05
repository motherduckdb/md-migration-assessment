"""Databricks report fact builders (adapter-owned ``report.*`` relations).

- ``report.sizing``: per table, type, format, bytes and files.
- ``report.readable_in_place``: per table, how MotherDuck could read it
  without copying (Unity Catalog Iceberg REST for Iceberg and UniForm
  tables, a direct Delta scan for external Delta), or what has to happen
  first. Facts and a read path only, no effort scores.

Like every fact builder: the relation always exists with a stable shape;
when the source extracts are missing it is empty and meta.extract_runs
says why.
"""

from __future__ import annotations

import duckdb

from ...report.facts import table_exists

_SYSTEM = (
    "(t.table_catalog IN ('system', 'samples', 'hive_metastore', '__databricks_internal') "
    "OR t.table_name LIKE '\\_\\_materialization\\_%' ESCAPE '\\' "
    "OR t.table_name LIKE 'event\\_log\\_%' ESCAPE '\\')"
)

_SIZING_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.sizing;
CREATE TABLE report.sizing (
    collection_id UUID, table_catalog VARCHAR, table_schema VARCHAR,
    table_name VARCHAR, table_type VARCHAR, data_source_format VARCHAR,
    bytes BIGINT, num_files BIGINT, is_system BOOLEAN,
    tables_extract_status VARCHAR, details_extract_status VARCHAR
);
"""

_READ_DDL = """
CREATE SCHEMA IF NOT EXISTS report;
DROP TABLE IF EXISTS report.readable_in_place;
CREATE TABLE report.readable_in_place (
    collection_id UUID, table_catalog VARCHAR, table_schema VARCHAR,
    table_name VARCHAR, table_type VARCHAR, data_source_format VARCHAR,
    uniform_formats VARCHAR, storage_scheme VARCHAR, read_path VARCHAR,
    note VARCHAR, tables_extract_status VARCHAR, details_extract_status VARCHAR
);
"""


def _details_cols(con: duckdb.DuckDBPyConnection) -> tuple[str, str]:
    """(select list, join) for raw.table_details, or NULL stand-ins."""
    if not table_exists(con, "raw", "table_details"):
        return (
            "CAST(NULL AS BIGINT) AS bytes, CAST(NULL AS BIGINT) AS num_files, "
            "CAST(NULL AS VARCHAR) AS uniform_formats, CAST(NULL AS VARCHAR) AS location, "
            "CAST(NULL AS VARCHAR) AS detail_format",
            "",
        )
    return (
        "d.size_in_bytes::BIGINT AS bytes, d.num_files::BIGINT AS num_files, "
        "d.uniform_formats AS uniform_formats, d.location AS location, "
        "d.format AS detail_format",
        "LEFT JOIN raw.table_details d ON d.collection_id = t.collection_id "
        "AND d.table_catalog = t.table_catalog AND d.table_schema = t.table_schema "
        "AND d.table_name = t.table_name",
    )


_BASE = """
    FROM raw.tables t
    LEFT JOIN meta.extract_runs tr
        ON tr.collection_id = t.collection_id AND tr.extractor = 'tables'
    LEFT JOIN meta.extract_runs dr
        ON dr.collection_id = t.collection_id AND dr.extractor = 'table_details'
    {join}
"""


def _build_sizing(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_SIZING_DDL)
    if not table_exists(con, "raw", "tables"):
        return
    cols, join = _details_cols(con)
    con.execute(f"""
        INSERT INTO report.sizing BY NAME
        SELECT * EXCLUDE (uniform_formats, location, detail_format) FROM (
            SELECT t.collection_id AS collection_id,
                   t.table_catalog AS table_catalog, t.table_schema AS table_schema,
                   t.table_name AS table_name, t.table_type AS table_type,
                   t.data_source_format AS data_source_format,
                   {cols},
                   {_SYSTEM} AS is_system,
                   tr.status AS tables_extract_status,
                   coalesce(dr.status, 'unavailable') AS details_extract_status
            {_BASE.format(join=join)}
            WHERE t.table_type NOT IN ('VIEW')
        )
    """)


def _build_readable_in_place(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(_READ_DDL)
    if not table_exists(con, "raw", "tables"):
        return
    cols, join = _details_cols(con)
    con.execute(f"""
        INSERT INTO report.readable_in_place BY NAME
        WITH base AS (
            SELECT t.collection_id AS collection_id,
                   t.table_catalog AS table_catalog, t.table_schema AS table_schema,
                   t.table_name AS table_name, t.table_type AS table_type,
                   t.data_source_format AS data_source_format,
                   {cols},
                   coalesce(location, t.storage_path) AS path,
                   tr.status AS tables_extract_status,
                   coalesce(dr.status, 'unavailable') AS details_extract_status
            {_BASE.format(join=join)}
            WHERE NOT {_SYSTEM}
        ), typed AS (
            SELECT *, upper(coalesce(data_source_format, detail_format, '')) AS fmt
            FROM base
        ), classified AS (
            SELECT *,
                lower(regexp_extract(coalesce(path, ''), '^([a-zA-Z0-9]+)://', 1)) AS storage_scheme,
                CASE
                    WHEN table_type IN ('VIEW', 'MATERIALIZED_VIEW', 'STREAMING_TABLE')
                        THEN 'recreate'
                    WHEN table_type = 'FOREIGN' THEN 'read_from_origin'
                    WHEN fmt = 'ICEBERG'
                        OR lower(coalesce(uniform_formats, '')) LIKE '%iceberg%'
                        THEN 'uc_iceberg_rest'
                    WHEN fmt = 'DELTA' AND table_type LIKE 'EXTERNAL%' THEN 'delta_scan'
                    WHEN fmt = 'DELTA' THEN 'enable_uniform_or_copy'
                    WHEN fmt IN ('PARQUET', 'CSV', 'JSON') THEN 'file_scan'
                    ELSE 'copy'
                END AS read_path
            FROM typed
        )
        SELECT collection_id, table_catalog, table_schema, table_name, table_type,
               data_source_format, uniform_formats,
               nullif(storage_scheme, '') AS storage_scheme, read_path,
               CASE
                   WHEN read_path = 'recreate'
                       THEN 'derived object: rebuild from its definition'
                   WHEN read_path = 'enable_uniform_or_copy'
                       THEN 'managed Delta without UniForm: enable Iceberg reads or copy'
                   WHEN details_extract_status <> 'complete' AND fmt = 'DELTA'
                       THEN 'UniForm unknown: table_details not complete'
                   WHEN storage_scheme IN ('abfss', 'gs', 'wasbs')
                       THEN 'storage on ' || storage_scheme || ': cross-cloud read'
               END AS note,
               tables_extract_status, details_extract_status
        FROM classified
    """)


FACT_BUILDERS = (_build_sizing, _build_readable_in_place)
