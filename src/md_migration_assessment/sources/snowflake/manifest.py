"""Snowflake extractor manifest.

Each extractor declares:

- which collection profile first includes it,
- its ACCOUNT_USAGE query and/or per-database INFORMATION_SCHEMA fallback
  (SQL lives in ``queries/`` as resource files), or a SHOW command,
- the columns usable for ``--scope`` filtering,
- the least Snowflake privilege that satisfies it (for the README matrix and
  for ``meta.extract_runs``),
- its sensitive fields, classified per :mod:`md_migration_assessment.privacy`.

The authoring vocabulary here is Snowflake's (``account_usage_sql``,
``info_schema_sql``, ``show_sql``); :func:`_ex` maps it onto the neutral
:class:`~md_migration_assessment.collect.extractor.Extractor` model, whose
ordered ``sources`` tuple the runner executes: SHOW is exclusive;
ACCOUNT_USAGE runs only at ``standard`` (lite has no ACCOUNT_USAGE grants)
and falls through to the INFORMATION_SCHEMA walk when unavailable.
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
from ...collect.extractor import extractors_for as _neutral_extractors_for
from ...privacy import PrivacyClass

# Version of the raw.* table shapes. Bump on any change to an extract's column
# set; schema migrations must be explicit (spec §3).
# v2 (M2): tables += is_iceberg/is_dynamic/is_hybrid; functions += packages/
#   runtime_version; new feature extracts (policies, tags, pipes, roles).
# v3 (M2 review): new feature extracts (tasks, stages, listings).
# v4 (M2 review follow-up): new shares extract.
# v5 (M3a): external_tables, usage-history feature extracts, SHOW-based
#   extracts (streams, warehouses, streamlits, notebooks, applications,
#   application packages, catalog integrations, shares listing).
# v6 (M3b): aggregate workload extracts (warehouse metering/load, daily
#   metering, copy/pipe/task history, login-derived driver inventory).
#   Per-query QUERY_HISTORY is deliberately absent (spec decision 15).
# v7 (M3b review): copy_history += load_status outcome rows (failed COPY
#   attempts are evidence, not writes); pipe_usage_history += pipe_id and
#   name-derived residency columns, hidden auto-refresh rows excluded.
# v8 (M3b review rounds 2-3): pipe_usage_history += source_kind — named
#   rows classified against ACCOUNT_USAGE.PIPES; unmatched rows are
#   'unclassified' (Iceberg automated refresh, an aged-out pipe, or a pipe
#   hidden from the collecting role), never presumed Snowpipe.
# v9 (M3c): server-side workload aggregates over QUERY_HISTORY — the GROUP
#   BY runs inside Snowflake, nothing per-query or textual ever lands
#   (spec decision 16): query_concurrency, query_tag_fingerprints,
#   client_app_fingerprints, query_shapes, query_workload_rollup,
#   query_dialect_constructs.
# v10 (M3c review rounds 1-2): query_concurrency rebuilt on exact event
#   timestamps with carriers bracketing the exact observation window
#   [window start, now - 45min QUERY_HISTORY latency watermark] — columns
#   become peak_concurrent_queries / avg_concurrent_queries / busy_seconds
#   (active_event_minutes dropped); the latency gap emits no rows and is
#   disclosed via actual_window_end; query_shapes exempts the '(unhashed)'
#   bucket from the top-N cap.
# v11 (M3d, decision 18 — Corrdyn review): inventory expansion. New extracts:
#   object_dependencies, table_read_heat (ACCESS_HISTORY read aggregate),
#   table/referential constraints, sequences, file_formats,
#   grants_to_roles_summary (aggregate only), dynamic_table_refresh_history,
#   and SHOW-based account_parameters, network_policies, storage/notification/
#   api/external-access integrations, external_volumes, dynamic_tables,
#   alerts, event_tables, replication/failover groups, resource_monitors.
RAW_SCHEMA_VERSION = 11

#: meta.extract_runs.source_used labels for this adapter's strategies.
ACCOUNT_USAGE = "account_usage"
INFORMATION_SCHEMA = "information_schema"
SHOW = "show"


def load_sql(kind: str, filename: str) -> str:
    """Load an extract SQL resource. kind is 'account_usage' or 'information_schema'."""
    return load_sql_path(f"{kind}/{filename}")


def load_sql_path(path: str) -> str:
    """Adapter-relative resource loader (the neutral core calls this one)."""
    root = resources.files("md_migration_assessment.sources.snowflake") / "queries"
    return root.joinpath(*path.split("/")).read_text(encoding="utf-8")


def extractor_version(ex: Extractor) -> str:
    return _neutral_version(ex, load_sql_path)


def _ex(
    name: str,
    *,
    category: str,
    min_profile: Profile,
    account_usage_sql: str | None = None,
    info_schema_sql: str | None = None,
    show_sql: str | None = None,
    expected_show_columns: tuple[str, ...] = (),
    account_usage_view: str | None = None,
    required_privilege: str = "SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES",
    min_edition: str = "STANDARD",
    **rest,
) -> Extractor:
    """Snowflake authoring shorthand -> neutral Extractor.

    - ``show_sql``: exclusive SHOW-command source; ``expected_show_columns``
      is its handoff allowlist (server-defined output has no projection).
    - ``account_usage_sql``: deployment-wide query, standard profile only.
      ``account_usage_view`` names the view read when it differs from the
      extract name (M3c aggregates all read QUERY_HISTORY).
    - ``info_schema_sql``: per-database walk, any profile.
    """
    sources: list = []
    if show_sql:
        if not expected_show_columns:
            raise ValueError(f"{name}: SHOW extract needs expected_show_columns")
        sources.append(Command(show_sql, tuple(expected_show_columns), SHOW))
    if account_usage_sql:
        sources.append(GlobalQuery(
            f"account_usage/{account_usage_sql}", ACCOUNT_USAGE,
            min_profile=Profile.STANDARD, source_view=account_usage_view,
        ))
    if info_schema_sql:
        sources.append(PerDatabaseQuery(f"information_schema/{info_schema_sql}", INFORMATION_SCHEMA))
    return Extractor(
        name=name,
        category=category,
        min_profile=min_profile,
        sources=tuple(sources),
        required_privilege=required_privilege,
        min_edition=min_edition,
        **rest,
    )


_CATALOG_SCOPE = {"database": "table_catalog", "schema": "table_schema"}

EXTRACTORS: list[Extractor] = [
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
        name="stage_storage_usage_history",
        category="sizing",
        min_profile=Profile.STANDARD,
        account_usage_sql="stage_storage_usage_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=365,
    ),
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
        name="cortex_ai_functions_usage_history",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="cortex_ai_functions_usage_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=90,
        sensitive_fields={"function_name": PrivacyClass.OBJECT_NAME},
    ),
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    _ex(
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
    # ── M3b: workload shape from aggregate histories ────────────────────
    # Per-query QUERY_HISTORY is deliberately not collected (spec decision
    # 15): these extracts are hour/day-grained aggregates whose size scales
    # with catalog and warehouse count, never with query volume. View↔role
    # mapping pinned against Snowflake docs 2026-08-19.
    _ex(
        name="warehouse_metering_history",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="warehouse_metering_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={"warehouse_name": PrivacyClass.OBJECT_NAME},
    ),
    _ex(
        name="warehouse_load_history",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="warehouse_load_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={"warehouse_name": PrivacyClass.OBJECT_NAME},
    ),
    _ex(
        name="metering_daily_history",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="metering_daily_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
    ),
    _ex(
        name="copy_history",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="copy_history.sql",
        info_schema_sql=None,
        scope_columns={"database": "table_catalog_name", "schema": "table_schema_name"},
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={
            "table_catalog": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="pipe_usage_history",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="pipe_usage_history.sql",
        info_schema_sql=None,
        # PIPE_USAGE_HISTORY has no residency columns; database/schema are
        # derived from the fully qualified PIPE_NAME so scoped runs never
        # persist out-of-scope pipe names (review finding, 2026-08-19).
        scope_columns={
            "database": "split_part(u.pipe_name, '.', 1)",
            "schema": "split_part(u.pipe_name, '.', 2)",
        },
        # + OBJECT_VIEWER: rows are classified against ACCOUNT_USAGE.PIPES —
        # a named row can be Iceberg automated refresh, not a pipe
        required_privilege=(
            "SNOWFLAKE.USAGE_VIEWER + SNOWFLAKE.OBJECT_VIEWER "
            "or IMPORTED PRIVILEGES"
        ),
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={
            "pipe_database": PrivacyClass.OBJECT_NAME,
            "pipe_schema": PrivacyClass.OBJECT_NAME,
            "pipe_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="task_history",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="task_history.sql",
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={
            "task_database": PrivacyClass.OBJECT_NAME,
            "task_schema": PrivacyClass.OBJECT_NAME,
            "task_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="login_history",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="login_history.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.SECURITY_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={"user_name": PrivacyClass.USER_IDENTITY},
    ),
    # ── M3c: server-side workload aggregates over QUERY_HISTORY ─────────
    # Spec decision 16: the GROUP BY runs inside Snowflake, so nothing
    # per-query and no query text ever crosses the wire — only counts,
    # opaque hashes, and derived labels land. Every extract here is
    # account-wide (QUERY_HISTORY has no reliable database residency; the
    # collected columns carry no database-resident object names).
    # View↔role mapping pinned against Snowflake docs 2026-08-19:
    # QUERY_HISTORY needs GOVERNANCE_VIEWER, SESSIONS needs SECURITY_VIEWER.
    _ex(
        name="query_concurrency",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="query_concurrency.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        account_usage_view="query_history",
        # the extract's observation spine stops at a 45-minute QUERY_HISTORY
        # latency watermark (documented max lag) so the not-yet-visible tail
        # is absent coverage, never rows that read as idle
        window_end_lag_minutes=45,
        sensitive_fields={"warehouse_name": PrivacyClass.OBJECT_NAME},
    ),
    _ex(
        name="query_tag_fingerprints",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="query_tag_fingerprints.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        account_usage_view="query_history",
        sensitive_fields={"warehouse_name": PrivacyClass.OBJECT_NAME},
    ),
    # split from query_tag_fingerprints so a customer withholding
    # SECURITY_VIEWER (needed for the SESSIONS join) still gets tag-based
    # tool attribution
    _ex(
        name="client_app_fingerprints",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="client_app_fingerprints.sql",
        info_schema_sql=None,
        required_privilege=(
            "SNOWFLAKE.GOVERNANCE_VIEWER + SNOWFLAKE.SECURITY_VIEWER "
            "or IMPORTED PRIVILEGES"
        ),
        window_days=30,
        window_from_history_days=True,
        account_usage_view="query_history",
        sensitive_fields={"warehouse_name": PrivacyClass.OBJECT_NAME},
    ),
    _ex(
        name="query_shapes",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="query_shapes.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        account_usage_view="query_history",
        sensitive_fields={"warehouse_name": PrivacyClass.OBJECT_NAME},
    ),
    _ex(
        name="query_workload_rollup",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="query_workload_rollup.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        account_usage_view="query_history",
        sensitive_fields={"warehouse_name": PrivacyClass.OBJECT_NAME},
    ),
    _ex(
        name="query_dialect_constructs",
        category="workload",
        min_profile=Profile.STANDARD,
        account_usage_sql="query_dialect_constructs.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        account_usage_view="query_history",
    ),
    # ── M3d: inventory expansion (Corrdyn review, decision 18) ──────────
    # View↔role mapping pinned against Snowflake docs 2026-08-20.
    _ex(
        name="object_dependencies",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="object_dependencies.sql",
        info_schema_sql=None,
        # scope selects referencing objects; referenced names are attributes
        # of the in-scope object (same semantics as view definitions)
        scope_columns={"database": "referencing_database", "schema": "referencing_schema"},
        required_privilege="SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "referencing_database": PrivacyClass.OBJECT_NAME,
            "referencing_schema": PrivacyClass.OBJECT_NAME,
            "referencing_object_name": PrivacyClass.OBJECT_NAME,
            "referenced_database": PrivacyClass.OBJECT_NAME,
            "referenced_schema": PrivacyClass.OBJECT_NAME,
            "referenced_object_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="table_read_heat",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="table_read_heat.sql",
        info_schema_sql=None,
        scope_columns={
            "database": 'split_part(o.value:"objectName"::VARCHAR, \'.\', 1)',
            "schema": 'split_part(o.value:"objectName"::VARCHAR, \'.\', 2)',
        },
        required_privilege="SNOWFLAKE.GOVERNANCE_VIEWER or IMPORTED PRIVILEGES",
        min_edition="ENTERPRISE",
        window_days=30,
        window_from_history_days=True,
        account_usage_view="access_history",
        sensitive_fields={
            "object_database": PrivacyClass.OBJECT_NAME,
            "object_schema": PrivacyClass.OBJECT_NAME,
            "object_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="table_constraints",
        category="features",
        min_profile=Profile.LITE,
        account_usage_sql="table_constraints.sql",
        info_schema_sql="table_constraints.sql",
        scope_columns=dict(_CATALOG_SCOPE),
        required_privilege="SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "table_catalog": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
            "constraint_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="referential_constraints",
        category="features",
        min_profile=Profile.LITE,
        account_usage_sql="referential_constraints.sql",
        info_schema_sql="referential_constraints.sql",
        scope_columns={"database": "constraint_catalog", "schema": "constraint_schema"},
        required_privilege="SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "constraint_catalog": PrivacyClass.OBJECT_NAME,
            "constraint_schema": PrivacyClass.OBJECT_NAME,
            "constraint_name": PrivacyClass.OBJECT_NAME,
            "unique_constraint_catalog": PrivacyClass.OBJECT_NAME,
            "unique_constraint_schema": PrivacyClass.OBJECT_NAME,
            "unique_constraint_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="sequences",
        category="features",
        min_profile=Profile.LITE,
        account_usage_sql=None,
        info_schema_sql="sequences.sql",
        scope_columns={"database": "sequence_catalog", "schema": "sequence_schema"},
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "sequence_catalog": PrivacyClass.OBJECT_NAME,
            "sequence_schema": PrivacyClass.OBJECT_NAME,
            "sequence_name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="grants_to_roles_summary",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="grants_to_roles_summary.sql",
        info_schema_sql=None,
        required_privilege="SNOWFLAKE.SECURITY_VIEWER or IMPORTED PRIVILEGES",
        account_usage_view="grants_to_roles",
        sensitive_fields={"role_name": PrivacyClass.USER_IDENTITY},
    ),
    _ex(
        name="account_parameters",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW PARAMETERS IN ACCOUNT",
        expected_show_columns=("key", "value", "default", "level", "description", "type"),
        required_privilege="any role",
        sensitive_fields={"value": PrivacyClass.COMMENT},
    ),
    _ex(
        name="network_policies",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        # SHOW output carries entry COUNTS only, never the IP lists themselves
        show_sql="SHOW NETWORK POLICIES",
        expected_show_columns=(
            "created_on", "name", "comment", "entries_in_allowed_ip_list",
            "entries_in_blocked_ip_list", "entries_in_allowed_network_rules",
            "entries_in_blocked_network_rules",
        ),
        required_privilege="any role (policies visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="storage_integrations",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW STORAGE INTEGRATIONS",
        expected_show_columns=("created_on", "name", "type", "category", "enabled", "comment"),
        required_privilege="any role (integrations visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="notification_integrations",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW NOTIFICATION INTEGRATIONS",
        expected_show_columns=("created_on", "name", "type", "category", "enabled", "comment"),
        required_privilege="any role (integrations visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="api_integrations",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW API INTEGRATIONS",
        expected_show_columns=("created_on", "name", "type", "category", "enabled", "comment"),
        required_privilege="any role (integrations visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="external_access_integrations",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW EXTERNAL ACCESS INTEGRATIONS",
        expected_show_columns=("created_on", "name", "enabled", "comment"),
        required_privilege="any role (integrations visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="file_formats",
        category="features",
        min_profile=Profile.LITE,
        account_usage_sql="file_formats.sql",
        info_schema_sql="file_formats.sql",
        scope_columns={"database": "file_format_catalog", "schema": "file_format_schema"},
        required_privilege="SNOWFLAKE.OBJECT_VIEWER or IMPORTED PRIVILEGES",
        sensitive_fields={
            "file_format_catalog": PrivacyClass.OBJECT_NAME,
            "file_format_schema": PrivacyClass.OBJECT_NAME,
            "file_format_name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="external_volumes",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW EXTERNAL VOLUMES",
        expected_show_columns=("name", "allow_writes", "comment"),
        required_privilege="any role (volumes visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="dynamic_tables",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        show_sql="SHOW DYNAMIC TABLES IN ACCOUNT",
        expected_show_columns=(
            "created_on", "name", "database_name", "schema_name", "rows",
            "bytes", "owner", "target_lag", "refresh_mode",
            "refresh_mode_reason", "warehouse", "text", "comment",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "warehouse": PrivacyClass.OBJECT_NAME,
            "text": PrivacyClass.SOURCE_BODY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="dynamic_table_refresh_history",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql="dynamic_table_refresh_history.sql",
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        required_privilege="SNOWFLAKE.USAGE_VIEWER or IMPORTED PRIVILEGES",
        window_days=30,
        window_from_history_days=True,
        sensitive_fields={
            "table_database": PrivacyClass.OBJECT_NAME,
            "table_schema": PrivacyClass.OBJECT_NAME,
            "table_name": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="alerts",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        show_sql="SHOW ALERTS IN ACCOUNT",
        expected_show_columns=(
            "created_on", "name", "database_name", "schema_name", "owner",
            "comment", "warehouse", "schedule", "state", "condition", "action",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "warehouse": PrivacyClass.OBJECT_NAME,
            "condition": PrivacyClass.SOURCE_BODY,
            "action": PrivacyClass.SOURCE_BODY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="event_tables",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        scope_columns={"database": "database_name", "schema": "schema_name"},
        show_sql="SHOW EVENT TABLES",
        expected_show_columns=(
            "created_on", "name", "database_name", "schema_name", "owner", "comment",
        ),
        required_privilege="any role (objects visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "database_name": PrivacyClass.OBJECT_NAME,
            "schema_name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
    _ex(
        name="replication_groups",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW REPLICATION GROUPS",
        expected_show_columns=(
            "created_on", "name", "type", "is_primary", "primary",
            "object_types", "allowed_databases", "allowed_shares",
            "replication_schedule",
        ),
        required_privilege="any role (groups visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "primary": PrivacyClass.USER_IDENTITY,
            "allowed_databases": PrivacyClass.OBJECT_NAME,
            "allowed_shares": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="failover_groups",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW FAILOVER GROUPS",
        expected_show_columns=(
            "created_on", "name", "type", "is_primary", "primary",
            "object_types", "allowed_databases", "allowed_shares",
            "replication_schedule",
        ),
        required_privilege="any role (groups visible to the role)",
        min_edition="BUSINESS CRITICAL",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "primary": PrivacyClass.USER_IDENTITY,
            "allowed_databases": PrivacyClass.OBJECT_NAME,
            "allowed_shares": PrivacyClass.OBJECT_NAME,
        },
    ),
    _ex(
        name="resource_monitors",
        category="features",
        min_profile=Profile.STANDARD,
        account_usage_sql=None,
        info_schema_sql=None,
        show_sql="SHOW RESOURCE MONITORS",
        expected_show_columns=(
            "name", "credit_quota", "used_credits", "remaining_credits",
            "level", "frequency", "start_time", "end_time", "suspend_at",
            "suspend_immediately_at", "created_on", "owner", "comment",
        ),
        required_privilege="any role (monitors visible to the role)",
        sensitive_fields={
            "name": PrivacyClass.OBJECT_NAME,
            "owner": PrivacyClass.USER_IDENTITY,
            "comment": PrivacyClass.COMMENT,
        },
    ),
]


def extractors_for(profile: Profile) -> list[Extractor]:
    return _neutral_extractors_for(EXTRACTORS, profile)


# ── Snowflake-vocabulary accessors over the neutral model ────────────────
# (README generation and the Snowflake test-suite read extractors in the
# adapter's own terms; the runner never uses these.)


def account_usage_sql(ex: Extractor) -> str | None:
    """ACCOUNT_USAGE extract filename (under queries/account_usage/), or None."""
    q = ex.global_query
    return q.sql.split("/", 1)[1] if q else None


def info_schema_sql(ex: Extractor) -> str | None:
    """INFORMATION_SCHEMA extract filename (under queries/information_schema/), or None."""
    q = ex.per_database_query
    return q.sql.split("/", 1)[1] if q else None


def show_sql(ex: Extractor) -> str | None:
    c = ex.command
    return c.command if c else None


def expected_show_columns(ex: Extractor) -> tuple[str, ...]:
    c = ex.command
    return c.expected_columns if c else ()


def account_usage_view(ex: Extractor) -> str | None:
    q = ex.global_query
    return q.source_view if q else None
