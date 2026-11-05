"""Databricks extractor manifest.

Unity Catalog exposes its catalog through ``information_schema`` views, one
set per catalog (``<catalog>.information_schema.*``) and a metastore-wide
set (``system.information_schema.*``). Both list only what the collecting
principal can see, so every per-catalog walk is ``visibility_bound``.

Table physical detail (format, files, bytes, Delta table features, UniForm)
is not in ``information_schema``; it comes from ``DESCRIBE DETAIL``, run
per table by the connection and returned as one client-materialized
command result (:data:`TABLE_DETAILS_COMMAND`).

v1 covers the catalog. v2 adds the workload rollup over
``system.query.history`` (standard profile). Billing, lineage, jobs and
pipelines are still planned signals.
"""

from __future__ import annotations

from importlib import resources

from ...collect.extractor import (
    Command,
    Extractor,
    GlobalQuery,
    PerDatabaseQuery,
    Profile,
)
from ...collect.extractor import extractor_version as _neutral_version
from ...privacy import PrivacyClass

# Version of the raw.* table shapes. Bump on any extract column change.
# v1: catalog inventory (information_schema walk + metastore objects) and
#   DESCRIBE DETAIL table details.
# v2: query_workload_rollup (system.query.history aggregate, standard) and
#   hive_metastore_tables (SHOW TABLES walk of the legacy Hive metastore).
RAW_SCHEMA_VERSION = 2

#: meta.extract_runs.source_used labels for this adapter's strategies.
INFORMATION_SCHEMA = "information_schema"
METASTORE = "system_information_schema"
DESCRIBE_DETAIL = "describe_detail"
SHOW_TABLES = "show_tables"
SYSTEM_TABLES = "system_tables"

#: Sentinel command the connection expands into one DESCRIBE DETAIL per
#: visible table (see connection.DatabricksSource.command).
TABLE_DETAILS_COMMAND = "DESCRIBE DETAIL <each visible table>"

#: Sentinel command for the legacy Hive metastore, which information_schema
#: does not cover: SHOW SCHEMAS IN hive_metastore, then SHOW TABLES per schema.
HIVE_TABLES_COMMAND = "SHOW TABLES <each hive_metastore schema>"
HIVE_TABLE_COLUMNS: tuple[str, ...] = (
    "table_catalog", "table_schema", "table_name", "list_error",
)

TABLE_DETAIL_COLUMNS: tuple[str, ...] = (
    "table_catalog", "table_schema", "table_name", "format", "location",
    "num_files", "size_in_bytes", "partition_columns", "clustering_columns",
    "table_features", "uniform_formats", "cdf_enabled", "dv_enabled",
    "column_mapping_mode", "detail_error",
)

_PRIV_CATALOG = "USE CATALOG + USE SCHEMA + BROWSE (or SELECT) on the objects"
_PRIV_METASTORE = "metastore-level object visibility (owner, admin, or explicit grant)"


def load_sql_path(path: str) -> str:
    """Adapter-relative resource loader (the neutral core calls this one)."""
    root = resources.files("md_migration_assessment.sources.databricks") / "queries"
    return root.joinpath(*path.split("/")).read_text(encoding="utf-8")


def extractor_version(ex: Extractor) -> str:
    return _neutral_version(ex, load_sql_path)


def _per_catalog(name: str, scope: dict[str, str], sensitive: dict, **rest) -> Extractor:
    return Extractor(
        name=name,
        category=rest.pop("category", "catalog"),
        min_profile=Profile.LITE,
        sources=(PerDatabaseQuery(
            f"information_schema/{name}.sql", INFORMATION_SCHEMA, visibility_bound=True,
        ),),
        scope_columns=scope,
        required_privilege=_PRIV_CATALOG,
        sensitive_fields=sensitive,
        **rest,
    )


def _metastore(
    name: str, sensitive: dict, scope: dict[str, str] | None = None, *, bound: bool = True
) -> Extractor:
    # Unity Catalog's system.information_schema lists only securables the
    # collecting principal has a privilege on, so a zero is not absence.
    return Extractor(
        name=name,
        category="platform",
        min_profile=Profile.LITE,
        sources=(GlobalQuery(f"metastore/{name}.sql", METASTORE, visibility_bound=bound),),
        scope_columns=scope or {},
        required_privilege=_PRIV_METASTORE,
        sensitive_fields=sensitive,
    )


_TBL_SCOPE = {"database": "table_catalog", "schema": "table_schema"}
_TBL_NAMES = {
    "table_catalog": PrivacyClass.OBJECT_NAME,
    "table_schema": PrivacyClass.OBJECT_NAME,
    "table_name": PrivacyClass.OBJECT_NAME,
}
_TAG_SCOPE = {"database": "catalog_name", "schema": "schema_name"}
_TAG_NAMES = {
    "catalog_name": PrivacyClass.OBJECT_NAME,
    "schema_name": PrivacyClass.OBJECT_NAME,
    "table_name": PrivacyClass.OBJECT_NAME,
    "tag_name": PrivacyClass.OBJECT_NAME,
    "tag_value": PrivacyClass.COMMENT,
}

EXTRACTORS: list[Extractor] = [
    _metastore("catalogs", {
        "catalog_name": PrivacyClass.OBJECT_NAME,
        "catalog_owner": PrivacyClass.USER_IDENTITY,
        "created_by": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }, scope={"database": "catalog_name"}),
    _per_catalog("schemata", {"database": "catalog_name", "schema": "schema_name"}, {
        "catalog_name": PrivacyClass.OBJECT_NAME,
        "schema_name": PrivacyClass.OBJECT_NAME,
        "schema_owner": PrivacyClass.USER_IDENTITY,
        "created_by": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }),
    _per_catalog("tables", dict(_TBL_SCOPE), {
        **_TBL_NAMES,
        "storage_path": PrivacyClass.OBJECT_NAME,
        "table_owner": PrivacyClass.USER_IDENTITY,
        "created_by": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }),
    _per_catalog("columns", dict(_TBL_SCOPE), {
        **_TBL_NAMES,
        "column_name": PrivacyClass.OBJECT_NAME,
        "comment": PrivacyClass.COMMENT,
    }),
    _per_catalog("views", dict(_TBL_SCOPE), {
        **_TBL_NAMES,
        "view_definition": PrivacyClass.SOURCE_BODY,
    }),
    _per_catalog("routines", {"database": "routine_catalog", "schema": "routine_schema"}, {
        "routine_catalog": PrivacyClass.OBJECT_NAME,
        "routine_schema": PrivacyClass.OBJECT_NAME,
        "routine_name": PrivacyClass.OBJECT_NAME,
        "routine_owner": PrivacyClass.USER_IDENTITY,
        "routine_definition": PrivacyClass.SOURCE_BODY,
        "comment": PrivacyClass.COMMENT,
    }),
    _per_catalog("table_constraints", dict(_TBL_SCOPE), {
        **_TBL_NAMES, "constraint_name": PrivacyClass.OBJECT_NAME,
    }),
    _per_catalog("column_masks", dict(_TBL_SCOPE), {
        **_TBL_NAMES,
        "column_name": PrivacyClass.OBJECT_NAME,
        "mask_name": PrivacyClass.OBJECT_NAME,
        "using_columns": PrivacyClass.OBJECT_NAME,
    }, category="features"),
    _per_catalog("row_filters", dict(_TBL_SCOPE), {
        **_TBL_NAMES,
        "filter_name": PrivacyClass.OBJECT_NAME,
        "target_columns": PrivacyClass.OBJECT_NAME,
    }, category="features"),
    _per_catalog("table_tags", dict(_TAG_SCOPE), dict(_TAG_NAMES), category="features"),
    _per_catalog("column_tags", dict(_TAG_SCOPE), {
        **_TAG_NAMES, "column_name": PrivacyClass.OBJECT_NAME,
    }, category="features"),
    _per_catalog("volumes", {"database": "volume_catalog", "schema": "volume_schema"}, {
        "volume_catalog": PrivacyClass.OBJECT_NAME,
        "volume_schema": PrivacyClass.OBJECT_NAME,
        "volume_name": PrivacyClass.OBJECT_NAME,
        "storage_location": PrivacyClass.OBJECT_NAME,
        "volume_owner": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }, category="features"),
    Extractor(
        name="table_details",
        category="sizing",
        min_profile=Profile.LITE,
        sources=(Command(
            TABLE_DETAILS_COMMAND, TABLE_DETAIL_COLUMNS, DESCRIBE_DETAIL,
            visibility_bound=True,
        ),),
        scope_columns=dict(_TBL_SCOPE),
        required_privilege="SELECT (or BROWSE) on each table",
        sensitive_fields={
            **_TBL_NAMES,
            "location": PrivacyClass.OBJECT_NAME,
            "partition_columns": PrivacyClass.OBJECT_NAME,
            "clustering_columns": PrivacyClass.OBJECT_NAME,
            "detail_error": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="hive_metastore_tables",
        category="catalog",
        min_profile=Profile.LITE,
        sources=(Command(
            HIVE_TABLES_COMMAND, HIVE_TABLE_COLUMNS, SHOW_TABLES, visibility_bound=True,
        ),),
        scope_columns=dict(_TBL_SCOPE),
        required_privilege="USAGE on hive_metastore schemas (legacy table ACLs) or workspace admin",
        sensitive_fields={**_TBL_NAMES, "list_error": PrivacyClass.OBJECT_NAME},
    ),
    _metastore("metastores", {"metastore_name": PrivacyClass.OBJECT_NAME}, bound=False),
    _metastore("external_locations", {
        "external_location_name": PrivacyClass.OBJECT_NAME,
        "url": PrivacyClass.OBJECT_NAME,
        "storage_credential_name": PrivacyClass.OBJECT_NAME,
        "external_location_owner": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }),
    _metastore("storage_credentials", {
        "storage_credential_name": PrivacyClass.OBJECT_NAME,
        "storage_credential_owner": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }),
    _metastore("connections", {
        "connection_name": PrivacyClass.OBJECT_NAME,
        "connection_owner": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }),
    _metastore("shares", {
        "share_name": PrivacyClass.OBJECT_NAME,
        "share_owner": PrivacyClass.USER_IDENTITY,
        "comment": PrivacyClass.COMMENT,
    }),
    # ── workload (standard profile: system tables need explicit grants) ──
    Extractor(
        name="query_workload_rollup",
        category="workload",
        min_profile=Profile.STANDARD,
        sources=(GlobalQuery(
            "system/query_workload_rollup.sql", SYSTEM_TABLES,
            min_profile=Profile.STANDARD, source_view="system.query.history",
        ),),
        required_privilege="USE CATALOG system + USE SCHEMA system.query + SELECT on system.query.history",
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={"warehouse_id": PrivacyClass.OBJECT_NAME},
    ),
]
