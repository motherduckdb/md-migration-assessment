"""Extractor registry.

Each extractor declares:

- which collection profile first includes it,
- its ACCOUNT_USAGE query and/or per-database INFORMATION_SCHEMA fallback
  (SQL lives in ``queries/`` as resource files, individually overridable),
- the columns usable for ``--scope`` filtering,
- the least Snowflake privilege that satisfies it (for the README matrix and
  for ``meta.extract_runs``),
- its sensitive fields, classified per :mod:`md_migration_assessment.privacy`.

The extractor version recorded in ``meta.extract_runs`` is a content hash of
the extract SQL, so query edits are visible in collection provenance without
manual version bookkeeping.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import IntEnum
from importlib import resources

from ..privacy import PrivacyClass


class Profile(IntEnum):
    """Collection profiles, ordered by privilege requirements."""

    LITE = 1
    STANDARD = 2
    FULL = 3

    @classmethod
    def parse(cls, name: str) -> "Profile":
        try:
            return cls[name.upper()]
        except KeyError:
            raise ValueError(f"unknown profile {name!r}; use lite|standard|full") from None


@dataclass(frozen=True)
class Extractor:
    name: str
    category: str  # 'catalog' | 'sizing' | 'features' | 'workload'
    min_profile: Profile
    #: SQL resource under queries/account_usage/, or None if no ACCOUNT_USAGE source.
    account_usage_sql: str | None
    #: SQL resource under queries/information_schema/ run once per database
    #: with {database} substituted, or None if no fallback exists.
    info_schema_sql: str | None
    #: raw-table columns usable for scope filtering, keyed by level.
    #: e.g. {"database": "table_catalog", "schema": "table_schema"}
    scope_columns: dict[str, str] = field(default_factory=dict)
    required_privilege: str = "SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES"
    min_edition: str = "STANDARD"
    sensitive_fields: dict[str, PrivacyClass] = field(default_factory=dict)
    #: for time-windowed extracts: default lookback, substituted as {window_days}.
    window_days: int | None = None

    @property
    def target_table(self) -> str:
        return self.name


def load_sql(kind: str, filename: str) -> str:
    """Load an extract SQL resource. kind is 'account_usage' or 'information_schema'."""
    root = resources.files("md_migration_assessment.collect") / "queries" / kind / filename
    return root.read_text(encoding="utf-8")


def extractor_version(ex: Extractor) -> str:
    """Short content hash over the extractor's SQL text(s)."""
    h = hashlib.sha256()
    if ex.account_usage_sql:
        h.update(load_sql("account_usage", ex.account_usage_sql).encode())
    if ex.info_schema_sql:
        h.update(load_sql("information_schema", ex.info_schema_sql).encode())
    return h.hexdigest()[:12]


_CATALOG_SCOPE = {"database": "table_catalog", "schema": "table_schema"}

EXTRACTORS: list[Extractor] = [
    Extractor(
        name="databases",
        category="catalog",
        min_profile=Profile.LITE,
        account_usage_sql="databases.sql",
        info_schema_sql="databases.sql",
        scope_columns={"database": "database_name"},
        sensitive_fields={
            "database_name": PrivacyClass.OBJECT_NAME,
            "database_owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="schemata",
        category="catalog",
        min_profile=Profile.LITE,
        account_usage_sql="schemata.sql",
        info_schema_sql="schemata.sql",
        scope_columns={"database": "catalog_name", "schema": "schema_name"},
        sensitive_fields={
            "catalog_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "schema_owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="tables",
        category="catalog",
        min_profile=Profile.LITE,
        account_usage_sql="tables.sql",
        info_schema_sql="tables.sql",
        scope_columns=dict(_CATALOG_SCOPE),
        sensitive_fields={
            "table_catalog": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
            "table_owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="columns",
        category="catalog",
        min_profile=Profile.LITE,
        account_usage_sql="columns.sql",
        info_schema_sql="columns.sql",
        scope_columns=dict(_CATALOG_SCOPE),
        sensitive_fields={
            "table_catalog": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
            "column_name": PrivacyClass.OBJECT_NAME,
            "column_default": PrivacyClass.SOURCE_BODY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="views",
        category="catalog",
        min_profile=Profile.LITE,
        account_usage_sql="views.sql",
        info_schema_sql="views.sql",
        scope_columns=dict(_CATALOG_SCOPE),
        sensitive_fields={
            "table_catalog": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
            "table_owner": PrivacyClass.USER_IDENTITY,
            "view_definition": PrivacyClass.SOURCE_BODY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="functions",
        category="catalog",
        min_profile=Profile.LITE,
        account_usage_sql="functions.sql",
        info_schema_sql="functions.sql",
        scope_columns={"database": "function_catalog", "schema": "function_schema"},
        sensitive_fields={
            "function_catalog": PrivacyClass.OBJECT_NAME,
            "function_schema": PrivacyClass.OBJECT_NAME,
            "function_name": PrivacyClass.OBJECT_NAME,
            "function_owner": PrivacyClass.USER_IDENTITY,
            "function_definition": PrivacyClass.SOURCE_BODY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="procedures",
        category="catalog",
        min_profile=Profile.LITE,
        account_usage_sql="procedures.sql",
        info_schema_sql="procedures.sql",
        scope_columns={"database": "procedure_catalog", "schema": "procedure_schema"},
        sensitive_fields={
            "procedure_catalog": PrivacyClass.OBJECT_NAME,
            "procedure_schema": PrivacyClass.OBJECT_NAME,
            "procedure_name": PrivacyClass.OBJECT_NAME,
            "procedure_owner": PrivacyClass.USER_IDENTITY,
            "procedure_definition": PrivacyClass.SOURCE_BODY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="table_storage_metrics",
        category="sizing",
        min_profile=Profile.STANDARD,
        account_usage_sql="table_storage_metrics.sql",
        info_schema_sql=None,
        scope_columns=dict(_CATALOG_SCOPE),
        required_privilege="SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "table_catalog": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="stage_storage_usage_history",
        category="sizing",
        min_profile=Profile.STANDARD,
        account_usage_sql="stage_storage_usage_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=365,
    ),
    Extractor(
        name="database_storage_usage_history",
        category="sizing",
        min_profile=Profile.STANDARD,
        account_usage_sql="database_storage_usage_history.sql",
        info_schema_sql=None,
        scope_columns={"database": "database_name"},
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={"database_name": PrivacyClass.OBJECT_NAME},
        window_days=365,
    ),
]


def extractors_for(profile: Profile) -> list[Extractor]:
    return [e for e in EXTRACTORS if e.min_profile <= profile]
