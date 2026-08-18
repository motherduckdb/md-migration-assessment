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
    #: SHOW-command source (e.g. "SHOW STREAMS IN ACCOUNT"). SHOW output is
    #: account-wide and cannot be scope-filtered; columns are server-defined.
    show_sql: str | None = None
    #: for SHOW extracts: the explicit column allowlist the handoff builder
    #: uses (SELECT extracts derive theirs from the SQL projection instead).
    expected_show_columns: tuple[str, ...] = ()

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
    if ex.show_sql:
        h.update(ex.show_sql.encode())
        h.update(",".join(ex.expected_show_columns).encode())
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
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
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
    # ── governance / platform feature evidence (M2) ────────────────────
    Extractor(
        name="masking_policies",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="masking_policies.sql",
        info_schema_sql=None,
        scope_columns={"database": "policy_catalog", "schema": "policy_schema"},
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        min_edition="ENTERPRISE",
        sensitive_fields={
            "policy_catalog": PrivacyClass.OBJECT_NAME,
            "policy_schema": PrivacyClass.OBJECT_NAME,
            "policy_name": PrivacyClass.OBJECT_NAME,
            "policy_owner": PrivacyClass.USER_IDENTITY,
            "policy_body": PrivacyClass.SOURCE_BODY,
            "policy_comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="row_access_policies",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="row_access_policies.sql",
        info_schema_sql=None,
        scope_columns={"database": "policy_catalog", "schema": "policy_schema"},
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        min_edition="ENTERPRISE",
        sensitive_fields={
            "policy_catalog": PrivacyClass.OBJECT_NAME,
            "policy_schema": PrivacyClass.OBJECT_NAME,
            "policy_name": PrivacyClass.OBJECT_NAME,
            "policy_owner": PrivacyClass.USER_IDENTITY,
            "policy_body": PrivacyClass.SOURCE_BODY,
            "policy_comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="policy_references",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="policy_references.sql",
        info_schema_sql=None,
        scope_columns={"database": "ref_database_name", "schema": "ref_schema_name"},
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        min_edition="ENTERPRISE",
        sensitive_fields={
            "policy_name": PrivacyClass.OBJECT_NAME,
            "ref_database_name": PrivacyClass.OBJECT_NAME,
            "ref_schema_name": PrivacyClass.OBJECT_NAME,
            "ref_entity_name": PrivacyClass.OBJECT_NAME,
            "ref_column_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="tags",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="tags.sql",
        info_schema_sql=None,
        scope_columns={"database": "tag_database", "schema": "tag_schema"},
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        min_edition="ENTERPRISE",
        sensitive_fields={
            "tag_database": PrivacyClass.OBJECT_NAME,
            "tag_schema": PrivacyClass.OBJECT_NAME,
            "tag_name": PrivacyClass.OBJECT_NAME,
            "tag_owner": PrivacyClass.USER_IDENTITY,
            "tag_comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="tag_references",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="tag_references.sql",
        info_schema_sql=None,
        scope_columns={"database": "object_database", "schema": "object_schema"},
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        min_edition="ENTERPRISE",
        sensitive_fields={
            "tag_name": PrivacyClass.OBJECT_NAME,
            "tag_value": PrivacyClass.COMMENT,
            "object_database": PrivacyClass.OBJECT_NAME,
            "object_schema": PrivacyClass.OBJECT_NAME,
            "object_name": PrivacyClass.OBJECT_NAME,
            "column_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="pipes",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="pipes.sql",
        info_schema_sql=None,
        scope_columns={"database": "pipe_catalog", "schema": "pipe_schema"},
        required_privilege="SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "pipe_catalog": PrivacyClass.OBJECT_NAME,
            "pipe_schema": PrivacyClass.OBJECT_NAME,
            "pipe_name": PrivacyClass.OBJECT_NAME,
            "pipe_owner": PrivacyClass.USER_IDENTITY,
            "definition": PrivacyClass.SOURCE_BODY,
            "notification_channel_name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="tasks",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="tasks.sql",
        info_schema_sql=None,
        scope_columns={"database": "task_database", "schema": "task_schema"},
        sensitive_fields={
            "task_database": PrivacyClass.OBJECT_NAME,
            "task_schema": PrivacyClass.OBJECT_NAME,
            "task_name": PrivacyClass.OBJECT_NAME,
            "task_owner": PrivacyClass.USER_IDENTITY,
            "warehouse": PrivacyClass.OBJECT_NAME,
            "predecessors": PrivacyClass.OBJECT_NAME,
            "definition": PrivacyClass.SOURCE_BODY,
            "condition": PrivacyClass.SOURCE_BODY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="stages",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="stages.sql",
        info_schema_sql=None,
        scope_columns={"database": "stage_catalog", "schema": "stage_schema"},
        sensitive_fields={
            "stage_catalog": PrivacyClass.OBJECT_NAME,
            "stage_schema": PrivacyClass.OBJECT_NAME,
            "stage_name": PrivacyClass.OBJECT_NAME,
            "stage_owner": PrivacyClass.USER_IDENTITY,
            "stage_url": PrivacyClass.OBJECT_NAME,
            "storage_integration": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="listings",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="listings.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.SECURITY_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "global_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "title": PrivacyClass.COMMENT,
            "share": PrivacyClass.OBJECT_NAME,
            "application_package": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="shares",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="shares.sql",
        info_schema_sql=None,
        # deliberately unscoped: DATABASE_NAME is NULL for shares with no
        # database granted yet, and a scope predicate would silently drop
        # them — coalescing missing evidence into zero
        required_privilege="SNOWFLAKE.SECURITY_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "listing_global_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "target_accounts": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    # ── M3a: completing the feature inventory ──────────────────────────
    Extractor(
        name="external_tables",
        category="features",
        min_profile=Profile.LITE,
        account_usage_sql=None,
        info_schema_sql="external_tables.sql",
        scope_columns=dict(_CATALOG_SCOPE),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "table_catalog": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
            "table_owner": PrivacyClass.USER_IDENTITY,
            "location": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="cortex_ai_functions_usage_history",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="cortex_ai_functions_usage_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=90,
        sensitive_fields={"function_name": PrivacyClass.OBJECT_NAME},
    ),
    Extractor(
        name="search_optimization_history",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="search_optimization_history.sql",
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        min_edition="ENTERPRISE",
        window_days=90,
        sensitive_fields={
            "database_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    Extractor(
        name="snowpipe_streaming_client_history",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="snowpipe_streaming_client_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=90,
        sensitive_fields={"client_name": PrivacyClass.OBJECT_NAME},
    ),
    # SHOW-command extracts: account-wide, columns are server-defined; the
    # expected_show_columns allowlist is what survives into a handoff.
    Extractor(
        name="streams",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        show_sql="SHOW STREAMS IN ACCOUNT",
        expected_show_columns=(
            "created_on", "name", "database_name", "schema_name", "owner",
            "comment", "table_name", "source_type", "base_tables", "type",
            "stale", "mode",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
            "base_tables": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="warehouses",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW WAREHOUSES",
        expected_show_columns=(
            "created_on", "name", "state", "type", "size",
            "min_cluster_count", "max_cluster_count", "auto_suspend",
            "auto_resume", "scaling_policy", "enable_query_acceleration",
            "query_acceleration_max_scale_factor", "resource_monitor",
            "generation", "resource_constraint", "owner", "comment",
        ),
        required_privilege="any role (warehouses visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="streamlit_apps",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        show_sql="SHOW STREAMLITS IN ACCOUNT",
        expected_show_columns=(
            "created_on", "name", "database_name", "schema_name", "title",
            "owner", "query_warehouse", "comment",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "title": PrivacyClass.COMMENT,
            "owner": PrivacyClass.USER_IDENTITY,
            "query_warehouse": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="notebooks",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        show_sql="SHOW NOTEBOOKS IN ACCOUNT",
        expected_show_columns=(
            "created_on", "name", "database_name", "schema_name", "owner",
            "query_warehouse", "comment",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "query_warehouse": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="applications",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW APPLICATIONS",
        expected_show_columns=(
            "created_on", "name", "source_type", "source", "owner",
            "version", "label", "comment",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "source": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="application_packages",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW APPLICATION PACKAGES",
        expected_show_columns=(
            "created_on", "name", "distribution", "owner", "comment",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="catalog_integrations",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW CATALOG INTEGRATIONS",
        expected_show_columns=(
            "created_on", "name", "type", "category", "enabled", "comment",
        ),
        required_privilege="any role (integrations visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="show_shares",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        scope_columns={"database": "database_name"},
        show_sql="SHOW SHARES",
        expected_show_columns=(
            "created_on", "kind", "owner_account", "name", "database_name",
            "owner", "comment", "listing_global_name", "secure_objects_only",
        ),
        required_privilege="any role (shares visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "listing_global_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "owner_account": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    Extractor(
        name="roles",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="roles.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.SECURITY_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "name": PrivacyClass.USER_IDENTITY,
            "owner": PrivacyClass.USER_IDENTITY,
            "role_database_name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
]


def extractors_for(profile: Profile) -> list[Extractor]:
    return [e for e in EXTRACTORS if e.min_profile <= profile]
