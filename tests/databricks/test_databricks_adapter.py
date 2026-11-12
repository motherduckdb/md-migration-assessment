"""Databricks adapter, end to end against a synthetic Unity Catalog."""

from __future__ import annotations

import json
import re

import duckdb
import pyarrow as pa
import pytest

from fake_databricks import FakeDatabricks

from md_migration_assessment.collect.extractor import Profile
from md_migration_assessment.collect.runner import Scope, run_collection
from md_migration_assessment.db import open_output
from md_migration_assessment.handoff import build_handoff
from md_migration_assessment.report import build_report
from md_migration_assessment.sources import SOURCE_KINDS, get_adapter
from md_migration_assessment.sources.base import Connection, SourceAdapter
from md_migration_assessment.sources.databricks import ADAPTER
from md_migration_assessment.sources.databricks.connection import (
    DatabricksConfig,
    DatabricksSource,
    detail_row,
    detail_targets_sql,
)
from md_migration_assessment.sources.databricks.manifest import (
    TABLE_DETAILS_COMMAND,
    load_sql_path,
)


def runs(con, coll):
    """Extract run status, source, scope and detail per extract, for one collection."""
    out = {}
    for name, status, used, scope, detail in con.execute(
        "SELECT extractor, status, source_used, actual_scope, error_detail "
        "FROM meta.extract_runs WHERE collection_id = ?", [str(coll.collection_id)],
    ).fetchall():
        out[name] = dict(status=status, source_used=used, actual_scope=scope, detail=detail)
    return out


def features(con, coll):
    """Feature inventory rows by name: status, count and sample objects."""
    return {
        r[0]: (r[1], r[2], r[3]) for r in con.execute(
            "SELECT feature, observation_status, count, sample_objects "
            "FROM report.feature_inventory WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    }


# ── contract ─────────────────────────────────────────────────────────────


def test_registry_and_protocols():
    """Databricks is a registered source, and the adapter and fake match the shared interfaces."""
    assert "databricks" in SOURCE_KINDS
    assert get_adapter("databricks") is ADAPTER
    assert isinstance(ADAPTER, SourceAdapter)
    assert isinstance(FakeDatabricks(), Connection)


def test_every_extractor_sql_resource_loads_and_has_scope_hook():
    """Every extract query loads, and scoped ones have the placeholder that --scope fills in."""
    # Catalog-scoped extracts must take --scope; account-level system tables
    # (billing, compute, lakeflow, audit) have no catalog column to scope by.
    for ex in ADAPTER.extractors:
        for s in ex.sources:
            if hasattr(s, "sql"):
                sql = load_sql_path(s.sql)
                if ex.scope_columns:
                    assert "{scope_filter}" in sql, s.sql


def _extract_text(ex) -> str:
    """Everything an extractor can produce columns from: its SQL files and command column lists."""
    parts = []
    for s in ex.sources:
        if hasattr(s, "sql"):
            parts.append(load_sql_path(s.sql))
        if hasattr(s, "expected_columns"):
            parts.append(" ".join(s.expected_columns))
    return "\n".join(parts).lower()


@pytest.mark.parametrize("ex", ADAPTER.extractors, ids=lambda e: e.name)
def test_sensitive_fields_reference_real_columns(ex):
    """Every column a privacy class is declared for appears in the extract, so a typo can't leave one unclassified."""
    text = _extract_text(ex)
    for column in ex.sensitive_fields:
        assert re.search(rf"\b{re.escape(column.lower())}\b", text), (
            f"{ex.name}: sensitive field {column!r} not in its SQL or command columns"
        )


@pytest.mark.parametrize("ex", ADAPTER.extractors, ids=lambda e: e.name)
def test_scope_columns_reference_real_columns(ex):
    """Every --scope column appears in the extract."""
    text = _extract_text(ex)
    for column in ex.scope_columns.values():
        assert re.search(rf"\b{re.escape(column.lower())}\b", text), f"{ex.name}: {column}"


def test_lineage_scope_keeps_out_of_scope_names_out():
    """With --scope, lineage keeps an edge only when every table it names is in scope."""
    from md_migration_assessment.collect.runner import _render
    sql = load_sql_path("system/table_lineage_rollup.sql")
    scoped = _render(sql, scope_pred="", window_days=30, scope=Scope.parse(["sales"], ADAPTER.scope))
    assert "{scope_match" not in scoped
    assert "(source_table_full_name IS NULL OR (source_table_catalog IN ('sales')))" in scoped
    assert "(target_table_full_name IS NULL OR (target_table_catalog IN ('sales')))" in scoped
    unscoped = _render(sql, scope_pred="", window_days=30)
    assert "(source_table_full_name IS NULL OR TRUE)" in unscoped


def test_publish_title_names_databricks():
    """A Databricks publish without --title gets a Databricks title, not the Snowflake one."""
    from md_migration_assessment.publish import TITLE_TEMPLATE
    assert ADAPTER.display_name == "Databricks"
    assert TITLE_TEMPLATE.format(source=ADAPTER.display_name).startswith("Databricks → MotherDuck")
    assert ADAPTER.deployment_noun == "Databricks workspace"


def test_storage_credentials_never_selects_the_secret():
    """The credential, connection options and URL columns are never selected."""
    def select_list(path):
        """Column names in an extract's SELECT list, ignoring comment lines."""
        sql = "\n".join(
            ln for ln in load_sql_path(path).splitlines() if not ln.lstrip().startswith("--")
        )
        return {c.strip() for c in sql.split("FROM")[0].removeprefix("SELECT").split(",")}

    assert "credential" not in select_list("metastore/storage_credentials.sql")
    assert not {"connection_options", "url"} & select_list("metastore/connections.sql")


@pytest.mark.parametrize("msg,kind", [
    ("[INSUFFICIENT_PERMISSIONS] User does not have USE CATALOG on Catalog 'x'", "unavailable"),
    ("PERMISSION_DENIED: metastore admin required", "unavailable"),
    ("[TABLE_OR_VIEW_NOT_FOUND] The table or view `a`.`b` cannot be found", "unavailable"),
    ("User does not have SELECT privilege on table", "unavailable"),
    ("[PARSE_SYNTAX_ERROR] Syntax error at or near 'FROM'", "failed"),
    ("connection reset by peer", "failed"),
])
def test_classify_error(msg, kind):
    """Permission and missing-object errors count as unavailable. Other errors count as failed."""
    assert ADAPTER.classify_error(RuntimeError(msg)) == kind


# ── collection ───────────────────────────────────────────────────────────


def test_lite_collection_is_complete_and_walks_catalogs(out_db):
    """A lite run completes its extracts, skips standard-only ones, records the source, and keeps secret columns out."""
    conn = FakeDatabricks()
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    st = runs(out_db, coll)
    assert {e.name for e in ADAPTER.extractors} == set(st)
    for ex in ADAPTER.extractors:
        if ex.min_profile == Profile.STANDARD:
            assert st.pop(ex.name)["status"] == "not_requested", ex.name
    assert all(r["status"] == "complete" for r in st.values()), st
    assert "role-visibility bound" in st["shares"]["detail"]
    assert st["metastores"]["detail"] is None
    assert st["tables"]["source_used"] == "information_schema"
    assert json.loads(st["tables"]["actual_scope"]) == ["assess_lab", "workspace"]
    assert "role-visibility bound" in st["tables"]["detail"]
    assert st["table_details"]["source_used"] == "describe_detail"
    row = out_db.execute(
        "SELECT source_kind, source_deployment, source_version, source_region "
        "FROM meta.collections").fetchone()
    assert row == ("databricks", "dbc-test.cloud.databricks.com", "2026.43", "aws:us-east-2")
    # information_schema's own views never land
    assert out_db.execute(
        "SELECT count(*) FROM raw.tables WHERE table_schema = 'information_schema'"
    ).fetchone()[0] == 0
    # the secret-bearing columns were never selected
    cols = {r[0] for r in out_db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name IN ('storage_credentials', 'connections')"
    ).fetchall()}
    assert not {"credential", "connection_options", "url"} & cols


def test_signals_count_the_seeded_features(out_db):
    """Each feature count matches the seeded estate. A zero from a visibility-bound listing is unknown."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.LITE)
    build_report(out_db)
    f = features(out_db, coll)
    expected = {
        "managed_iceberg_tables": 1, "uniform_iceberg_tables": 1, "external_tables": 1,
        "shallow_clones": 1, "materialized_views": 1, "streaming_tables": 0,
        "partitioned_tables": 1, "liquid_clustering": 1, "deletion_vectors": 1,
        "change_data_feed": 1, "column_mapping": 1, "identity_columns": 1,
        "generated_columns": 1, "variant_columns": 1, "complex_type_columns": 1,
        "python_udfs": 1, "sql_udfs": 3, "column_masks": 1, "row_filters": 1,
        "table_tags": 1, "column_tags": 1, "informational_constraints": 2,
        "volumes": 1, "federation_connections": 1, "delta_shares": 0,
        "table_detail_errors": 0, "non_delta_formats": 0,
    }
    for name, n in expected.items():
        status, count, _ = f[name]
        if n:
            assert (status, count) == ("observed", n), (name, f[name])
        else:
            # zero from a visibility-bound listing (per-catalog or the
            # metastore-wide system.information_schema) is missing evidence
            assert (status, count) == ("unknown", None), (name, f[name])
    # metastore-level counts are lower bounds too
    assert f["federation_connections"][0] == "observed"
    # hidden MV furniture is not counted as user tables
    assert f["materialized_views"][2] == ["assess_lab.marts.daily_revenue"]
    assert f["hive_metastore_tables"] == ("observed", 2, [
        "hive_metastore.legacy_sales.customers_2019", "hive_metastore.legacy_sales.orders_2019"])


def test_scope_filters_sql_and_command_output(out_db):
    """Scope limits both the SQL extracts and the table details, and other catalogs are never queried."""
    conn = FakeDatabricks()
    scope = Scope.parse(["assess_lab.core"], ADAPTER.scope)
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE, scope=scope)
    st = runs(out_db, coll)
    assert st["tables"]["status"] == "complete"
    names = {r[0] for r in out_db.execute("SELECT table_name FROM raw.tables").fetchall()}
    assert names == {"customers", "products", "orders", "order_items"}
    details = {r[0] for r in out_db.execute(
        "SELECT table_catalog || '.' || table_schema FROM raw.table_details").fetchall()}
    assert details == {"assess_lab.core"}
    assert "filtered client-side" in st["table_details"]["detail"]
    # the detail query is limited by scope before its cap, not only after
    assert "assess_lab" in conn.scope_predicate
    # the out-of-scope catalog is never walked
    assert not any("`workspace`" in q for q in conn.queries)


def test_scope_entries_are_case_folded_to_unity_catalog_lower_case():
    """Scope names are lower-cased to match Unity Catalog."""
    scope = Scope.parse(["Assess_Lab"], ADAPTER.scope)
    assert scope.databases == frozenset({"assess_lab"})


def test_privilege_gap_on_one_catalog_is_partial(out_db):
    """A missing grant on one catalog marks the run partial, not failed."""
    conn = FakeDatabricks(denied_catalogs={"workspace"})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    row = runs(out_db, coll)["tables"]
    assert row["status"] == "partial"
    assert json.loads(row["actual_scope"]) == ["assess_lab"]


def test_unavailable_metastore_view_reports_unknown_not_zero(out_db):
    """A denied metastore view shows as unknown in the report, not zero."""
    conn = FakeDatabricks(denied_views={"connections"})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    assert runs(out_db, coll)["connections"]["status"] == "unavailable"
    build_report(out_db)
    assert features(out_db, coll)["federation_connections"][0] == "unknown"


def test_detail_errors_are_kept_per_table(out_db):
    """A failed DESCRIBE DETAIL on one table is recorded and counted, and the run still completes."""
    conn = FakeDatabricks(detail_errors={
        ("assess_lab", "core", "customers"): "[INSUFFICIENT_PERMISSIONS] no SELECT",
    })
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    assert runs(out_db, coll)["table_details"]["status"] == "complete"
    build_report(out_db)
    assert features(out_db, coll)["table_detail_errors"][1] == 1


# ── report facts ─────────────────────────────────────────────────────────


def test_readable_in_place_paths(out_db):
    """Each table gets the right way to read it from MotherDuck, and hidden tables are left out."""
    run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.LITE)
    build_report(out_db)
    paths = dict(out_db.execute(
        "SELECT table_schema || '.' || table_name, read_path FROM report.readable_in_place "
        "WHERE table_catalog = 'assess_lab'").fetchall())
    assert paths["core.order_items"] == "uc_iceberg_rest"      # UniForm
    assert paths["marts.region_targets"] == "uc_iceberg_rest"  # managed Iceberg
    assert paths["raw.ext_clicks"] == "delta_scan"             # external Delta
    assert paths["core.customers"] == "enable_uniform_or_copy"
    assert paths["marts.customer_ltv"] == "recreate"
    assert paths["marts.daily_revenue"] == "recreate"
    assert not any(k.startswith("marts.__materialization") for k in paths)
    schemes = {r[0] for r in out_db.execute(
        "SELECT DISTINCT storage_scheme FROM report.readable_in_place "
        "WHERE storage_scheme IS NOT NULL").fetchall()}
    assert schemes == {"s3"}


def test_uniform_is_unknown_when_table_details_are_incomplete(out_db):
    """A managed Delta table that was never described is "unknown", not "without UniForm"."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.LITE)
    cid = str(coll.collection_id)
    # as if the detail cap stopped before this table
    out_db.execute("UPDATE meta.extract_runs SET status = 'partial' "
                   "WHERE collection_id = ? AND extractor = 'table_details'", [cid])
    out_db.execute("DELETE FROM raw.table_details WHERE table_name = 'customers'")
    build_report(out_db)
    row = out_db.execute(
        "SELECT read_path, note FROM report.readable_in_place "
        "WHERE table_catalog = 'assess_lab' AND table_schema = 'core' AND table_name = 'customers'"
    ).fetchone()
    assert row == ("unknown", "UniForm unknown: table_details not complete")


def test_serverless_compute_has_one_name_in_spend_and_workload(out_db):
    """Serverless jobs and notebooks get the same name in spend and workload, so one filter matches both."""
    run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    build_report(out_db)
    spend = {r[0] for r in out_db.execute("SELECT DISTINCT warehouse_name FROM report.spend_profile").fetchall()}
    work = {r[0] for r in out_db.execute("SELECT DISTINCT warehouse_name FROM report.workload_rollup").fetchall()}
    assert "serverless compute" in spend & work
    assert "SERVERLESS_COMPUTE" not in work and "jobs" not in spend


def test_sizing_joins_details_and_flags_system_furniture(out_db):
    """Sizing takes bytes and files from table details, marks hidden tables as system, and skips views."""
    run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.LITE)
    build_report(out_db)
    rows = {r[0]: r[1:] for r in out_db.execute(
        "SELECT table_name, bytes, num_files, is_system, details_extract_status "
        "FROM report.sizing").fetchall()}
    assert rows["orders"] == (1000, 4, False, "complete")
    assert rows["__materialization_mat_daily_revenue_1"][2] is True
    assert "customer_ltv" not in rows  # plain views carry no storage


def test_fact_relations_exist_without_evidence(out_db):
    """The report tables still exist when the tables listing is denied."""
    conn = FakeDatabricks(denied_views={"tables"})
    run_collection(out_db, ADAPTER, conn, profile=Profile.LITE)
    build_report(out_db)
    for rel in ("report.sizing", "report.readable_in_place"):
        assert out_db.execute(f"SELECT count(*) FROM {rel}").fetchone()[0] >= 0


# ── handoff ──────────────────────────────────────────────────────────────


def test_handoff_strips_source_bodies(tmp_path):
    """The published handoff drops view and function bodies."""
    src = str(tmp_path / "private.duckdb")
    con = open_output(src)
    run_collection(con, ADAPTER, FakeDatabricks(), profile=Profile.LITE)
    build_report(con)
    con.close()
    manifest = build_handoff(src, str(tmp_path / "handoff.duckdb"))
    assert manifest["tables"]["raw.views"]["excluded_columns"] == ["view_definition"]
    assert manifest["tables"]["raw.routines"]["excluded_columns"] == ["routine_definition"]
    assert manifest["tables"]["raw.table_details"]["dropped_unexpected"] == []


# ── connection (no network) ──────────────────────────────────────────────


def test_config_from_env(monkeypatch):
    """Config reads host, warehouse and login from the environment and picks the matching auth mode."""
    for k in ("DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET",
              "DATABRICKS_HTTP_PATH", "DATABRICKS_DETAIL_MAX_TABLES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbc-test.cloud.databricks.com/")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123")
    cfg = DatabricksConfig.from_env()
    assert cfg.host == "dbc-test.cloud.databricks.com"
    assert cfg.http_path == "/sql/1.0/warehouses/abc123"
    assert cfg.auth_mode == "u2m"
    assert cfg.connect_kwargs()["auth_type"] == "databricks-oauth"
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")
    assert DatabricksConfig.from_env().connect_kwargs()["access_token"] == "dapi-test"


def test_config_requires_host_and_warehouse(monkeypatch):
    """Config fails with a clear error when the host or warehouse is missing."""
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    with pytest.raises(ValueError, match="DATABRICKS_HOST"):
        DatabricksConfig.from_env()
    monkeypatch.setenv("DATABRICKS_HOST", "h")
    monkeypatch.delenv("DATABRICKS_HTTP_PATH", raising=False)
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
    with pytest.raises(ValueError, match="WAREHOUSE_ID"):
        DatabricksConfig.from_env()


def test_detail_row_flattens_describe_detail():
    """A DESCRIBE DETAIL result becomes one flat row, with empty values as None."""
    row = detail_row("c", "s", "t", {
        "format": "delta", "location": "", "numFiles": 2, "sizeInBytes": 10,
        "partitionColumns": ["d"], "clusteringColumns": [],
        "tableFeatures": ["deletionVectors", "appendOnly"],
        "properties": [("delta.universalFormat.enabledFormats", "iceberg"),
                       ("delta.enableDeletionVectors", "true")],
    })
    assert row["location"] is None
    assert row["partition_columns"] == "d" and row["clustering_columns"] is None
    assert row["table_features"] == "appendOnly,deletionVectors"
    assert row["uniform_formats"] == "iceberg" and row["dv_enabled"] is True


class _Cursor:
    """Fake cursor: lists three tables, fails to describe table b, and describes the rest."""
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.owner.sql.append(sql)
        self.sql = sql

    def fetchall_arrow(self):
        if self.sql.startswith("SELECT table_catalog"):
            return pa.table({"table_catalog": ["c"] * 3, "table_schema": ["s"] * 3,
                             "table_name": ["a", "b", "c"]})
        if "`b`" in self.sql:
            raise RuntimeError("[INSUFFICIENT_PERMISSIONS] nope")
        return pa.Table.from_pylist([{"format": "delta", "numFiles": 1, "sizeInBytes": 5,
                                      "properties": []}])


class _DbApi:
    """Fake connection that hands out cursors and records every statement."""
    def __init__(self):
        self.sql = []

    def cursor(self):
        return _Cursor(self)

    def close(self):
        pass


def test_source_command_caps_and_isolates_per_table_errors():
    """Table details stop at the cap and say so, and an error on one table does not stop the rest."""
    api = _DbApi()
    src = DatabricksSource(api, detail_max_tables=2)
    table, truncated = src.command(TABLE_DETAILS_COMMAND)
    assert truncated is True
    assert table.column("table_name").to_pylist() == ["a", "b"]
    assert table.column("detail_error").to_pylist()[1].startswith("[INSUFFICIENT")
    assert "LIMIT 3" in api.sql[0]
    assert "DESCRIBE DETAIL `c`.`s`.`a`" in api.sql


def test_detail_targets_skip_views_and_hidden_tables():
    """The table list for DESCRIBE DETAIL leaves out views, hidden tables and system catalogs."""
    sql = detail_targets_sql(10)
    assert "'VIEW'" not in sql and "!_!_materialization!_" in sql and "'samples'" in sql


# ── v2: workload, warnings, handoff classification ──────────────────────


def test_standard_collects_workload_rollup_without_statement_text(out_db):
    """The workload rollup counts only in-window queries and never stores query text or user names."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    st = runs(out_db, coll)
    assert st["query_workload_rollup"]["status"] == "complete"
    assert st["query_workload_rollup"]["source_used"] == "system_tables"
    cols = {r[0] for r in out_db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'raw' AND table_name = 'query_workload_rollup'").fetchall()}
    assert not {"statement_text", "executed_by"} & cols
    rows = out_db.execute(
        "SELECT compute_type, statement_type, sum(n_queries), sum(n_succeeded), "
        "sum(n_from_result_cache) FROM raw.query_workload_rollup GROUP BY ALL ORDER BY ALL"
    ).fetchall()
    # the statement outside the 30-day window is not counted
    assert rows == [("SERVERLESS_COMPUTE", "REPLACE", 1, 1, 0), ("WAREHOUSE", "SELECT", 4, 3, 1)]
    build_report(out_db)
    status, n, samples = features(out_db, coll)["query_workload"]
    assert (status, n, samples) == ("observed", 5, ["REPLACE", "SELECT"])


def test_workload_without_system_grant_is_unknown_not_zero(out_db):
    """Without access to query history, the workload shows as unknown, not zero."""
    conn = FakeDatabricks(denied_views={"query_history"})
    coll = run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD)
    assert runs(out_db, coll)["query_workload_rollup"]["status"] == "unavailable"
    build_report(out_db)
    assert features(out_db, coll)["query_workload"][0] == "unknown"


def test_collection_warnings_flag_filtered_and_masked_tables(out_db):
    """Tables with a row filter or column mask are flagged with their policy function."""
    run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.LITE)
    build_report(out_db)
    rows = out_db.execute(
        "SELECT warning, table_schema || '.' || table_name, policy_function "
        "FROM report.collection_warnings ORDER BY ALL").fetchall()
    assert [r[0] for r in rows] == ["column_mask", "row_filter"]
    assert all(r[2] for r in rows)


def test_report_columns_are_all_classified_for_handoff(out_db):
    """Every text column in the report tables has a privacy class, so none is left unclassified at publish."""
    from md_migration_assessment.handoff import classify_aggregate_columns

    run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    build_report(out_db)
    for (table,) in out_db.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'report'"
    ).fetchall():
        cols = [r[0] for r in out_db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'report' AND table_name = ? "
            "AND (data_type LIKE '%VARCHAR%' OR data_type = 'UUID')", [table]).fetchall()]
        assert classify_aggregate_columns(f"report.{table}", cols)[1] == [], table


def test_hive_metastore_walk_records_schema_errors_and_disabled_hms(out_db, tmp_path):
    """A schema that cannot be listed is recorded, and a disabled Hive metastore shows as unavailable."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.LITE)
    errs = out_db.execute(
        "SELECT table_schema, list_error FROM raw.hive_metastore_tables "
        "WHERE list_error IS NOT NULL").fetchall()
    assert [e[0] for e in errs] == ["locked"] and "INSUFFICIENT_PERMISSIONS" in errs[0][1]

    other = open_output(str(tmp_path / "nohms.duckdb"))
    coll = run_collection(other, ADAPTER, FakeDatabricks(hive=None), profile=Profile.LITE)
    run = runs(other, coll)["hive_metastore_tables"]
    assert run["status"] == "unavailable" and "UC_HIVE_METASTORE_DISABLED" in run["detail"]
    build_report(other)
    assert features(other, coll)["hive_metastore_tables"][0] == "unknown"


def test_hive_metastore_walk_caps_tables():
    """The Hive table listing stops at the cap and says so."""
    from md_migration_assessment.sources.databricks.connection import hive_tables_table

    table, truncated = hive_tables_table(["a", "b"], lambda s: [s + "1", s + "2"], 3)
    assert truncated and table.num_rows == 3


def test_standard_builds_dive_report_tables_and_system_signals(out_db):
    """A Databricks collection fills the same report.* relations the Dive reads."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    st = runs(out_db, coll)
    for name in ("warehouses", "jobs", "pipelines", "billing_usage_daily", "grants_summary",
                 "table_lineage_rollup", "query_concurrency", "client_app_fingerprints",
                 "query_dialect_constructs", "workspace_feature_usage"):
        assert st[name]["status"] == "complete", (name, st[name])
    build_report(out_db)
    count = lambda t: out_db.execute(f"SELECT count(*) FROM report.{t}").fetchone()[0]  # noqa: E731
    for t in ("spend_profile", "workload_rollup", "concurrency_profile",
              "tool_fingerprints", "ingestion_inventory", "dialect_constructs"):
        assert count(t) > 0, t
    # warehouse ids resolve to names; non-warehouse compute keeps its type
    names = {r[0] for r in out_db.execute(
        "SELECT DISTINCT warehouse_name FROM report.workload_rollup").fetchall()}
    assert "wh1" not in names
    # lineage targets in the system catalog never show up as ingestion
    assert out_db.execute(
        "SELECT count(*) FROM report.ingestion_inventory WHERE table_catalog = 'system'"
    ).fetchone()[0] == 0
    feats = dict(out_db.execute(
        "SELECT feature, observation_status FROM report.feature_inventory").fetchall())
    for f in ("lakeflow_jobs", "lakeflow_pipelines", "sql_warehouses", "table_lineage_edges"):
        assert feats[f] == "observed", (f, feats[f])
    assert feats["delta_sharing_recipients"] == "unknown"


def test_dbu_usage_counts_only_dbus(out_db):
    """Storage billed in GB-months is not added to the DBU total."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    cid = str(coll.collection_id)
    dbus = out_db.execute(
        "SELECT round(sum(usage_quantity))::BIGINT FROM raw.billing_usage_daily "
        "WHERE collection_id = ? AND upper(coalesce(usage_unit, 'DBU')) = 'DBU'", [cid]).fetchone()[0]
    # a large storage row that must not move the DBU count
    out_db.execute(
        "INSERT INTO raw.billing_usage_daily BY NAME SELECT * REPLACE "
        "('GB_MONTH' AS usage_unit, 'DEFAULT_STORAGE' AS billing_origin_product, 1e6 AS usage_quantity) "
        "FROM raw.billing_usage_daily WHERE collection_id = ? LIMIT 1", [cid])
    build_report(out_db)
    status, n, sample = features(out_db, coll)["dbu_usage"]
    assert (status, n) == ("observed", dbus)
    assert "DEFAULT_STORAGE" not in (sample or [])


def test_scoped_lineage_keeps_out_of_scope_tables_out(out_db):
    """With --scope lab.marts, the edge from lab.core is dropped, so its name never lands in raw."""
    scope = Scope.parse(["lab.marts"], ADAPTER.scope)
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD, scope=scope)
    assert runs(out_db, coll)["table_lineage_rollup"]["status"] == "complete"
    rows = out_db.execute("SELECT * FROM raw.table_lineage_rollup").fetchall()
    assert rows and not any("core" in str(r) for r in rows)


def test_concurrency_carries_a_long_query_into_the_next_hour(out_db):
    """A query running from 10:30 to 12:30 counts toward the 11:00 and 12:00 peaks."""
    conn = FakeDatabricks()
    conn.db.execute(
        "INSERT INTO query_history (compute, statement_type, execution_status, start_time, end_time, "
        "from_result_cache, total_duration_ms, statement_text, executed_by) VALUES "
        "({'type': 'WAREHOUSE', 'warehouse_id': 'wh2'}, 'SELECT', 'FINISHED', "
        "TIMESTAMP '2029-12-30 10:30', TIMESTAMP '2029-12-30 12:30', false, 7200000, 'long', 'seed@example.com')")
    run_collection(out_db, ADAPTER, conn, profile=Profile.STANDARD)
    peaks = dict(out_db.execute(
        "SELECT hour(hour_start), peak_concurrent_queries FROM raw.query_concurrency "
        "WHERE warehouse_id = 'wh2'").fetchall())
    assert peaks == {10: 1, 11: 1, 12: 1}


def test_handoff_keeps_every_databricks_output_column(out_db, tmp_path):
    """Each extract's real output columns match its parsed projection, so publish drops nothing."""
    from md_migration_assessment.db import open_output
    from md_migration_assessment.handoff import build_handoff
    src = tmp_path / "private.duckdb"
    con = open_output(str(src))
    run_collection(con, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    build_report(con)
    con.close()
    manifest = build_handoff(str(src), str(tmp_path / "handoff.duckdb"))
    dropped = {t: v["dropped_unexpected"] for t, v in manifest["tables"].items() if v["dropped_unexpected"]}
    assert dropped == {}


# ── job definitions (Jobs API) ───────────────────────────────────────────


def test_job_tasks_classify_code_without_paths_or_parameters(out_db):
    """Job tasks keep only type, language and compute. Paths, parameters and code never land."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    assert runs(out_db, coll)["job_tasks"]["status"] == "complete"
    rows = out_db.execute(
        "SELECT job_name, task_type, in_for_each, code_source, file_language, compute_kind, "
        "spark_version, library_kinds, trigger_kind FROM raw.job_tasks ORDER BY job_id, task_index"
    ).fetchall()
    assert rows == [
        ("assess_lab_seed_job", "notebook", False, "WORKSPACE", "scala", "job_cluster",
         "15.4.x-scala2.12", "maven,pypi", "cron"),
        ("assess_lab_seed_job", "sql", False, None, "sql", "sql_warehouse", None, None, "cron"),
        ("assess_lab_seed_job", "spark_jar", False, None, "jvm", "all_purpose_cluster",
         None, None, "cron"),
        ("landing_ingest", "notebook", False, "GIT", "python", "serverless", None, None,
         "file_arrival"),
        ("landing_ingest", "spark_python", True, "GIT", "python", "serverless", None, None,
         "file_arrival"),
        ("landing_ingest", "dbt", False, "GIT", "sql", "serverless", None, None, "file_arrival"),
    ]
    dump = str(out_db.execute("SELECT * FROM raw.job_tasks").fetchall())
    for leaked in ("someone@example.com", "do-not-store", "autoloader.py", "s3://", "com.example"):
        assert leaked not in dump, leaked
    build_report(out_db)
    f = features(out_db, coll)
    assert f["notebook_job_tasks"][:2] == ("observed", 2)
    assert f["python_job_tasks"][:2] == ("observed", 2)
    assert f["scala_or_jvm_job_tasks"][:2] == ("observed", 2)
    assert f["sql_or_dbt_job_tasks"][:2] == ("observed", 2)
    assert f["all_purpose_cluster_job_tasks"][:2] == ("observed", 1)
    assert f["event_triggered_jobs"] == ("observed", 1, ["landing_ingest (file_arrival)"])


def test_job_tasks_without_jobs_access_is_unknown_not_zero(out_db):
    """No Jobs API access shows the job features as unknown, not zero."""
    coll = run_collection(out_db, ADAPTER, FakeDatabricks(jobs_denied=True),
                          profile=Profile.STANDARD)
    assert runs(out_db, coll)["job_tasks"]["status"] == "unavailable"
    build_report(out_db)
    assert features(out_db, coll)["notebook_job_tasks"][0] == "unknown"


def test_job_task_rows_cap_notebook_lookups():
    """Notebook language lookups stop at the cap and report it."""
    from md_migration_assessment.sources.databricks.connection import job_task_rows

    jobs = [{"job_id": i, "settings": {"tasks": [
        {"notebook_task": {"notebook_path": f"/nb{i}"}}]}} for i in range(3)]
    calls = []
    rows, capped = job_task_rows(jobs, lambda p: calls.append(p) or "PYTHON", 2)
    assert capped is True and len(calls) == 2
    assert [r["file_language"] for r in rows] == ["python", "python", None]


# ── workspace scope ──────────────────────────────────────────────────────

from md_migration_assessment.sources.databricks.connection import (  # noqa: E402
    parse_workspaces,
    render_workspace_filter,
)


@pytest.mark.parametrize("value, expected", [
    (None, "current"), ("", "current"), (" current ", "current"), ("ALL", "all"),
    ("123, 456", ("123", "456")),
])
def test_parse_workspaces(value, expected):
    """DATABRICKS_WORKSPACES is 'current' (default), 'all', or a list of workspace IDs."""
    assert parse_workspaces(value) == expected


def test_parse_workspaces_rejects_names():
    """Anything other than numeric IDs is refused, so it can never reach the SQL."""
    with pytest.raises(ValueError):
        parse_workspaces("prod; DROP TABLE x")


def test_render_workspace_filter():
    """The placeholder becomes an IN list, with an optional table alias, or nothing for all."""
    sql = "WHERE a {workspace_filter} AND b {workspace_filter:u}"
    assert render_workspace_filter(sql, ("1", "2")) == (
        "WHERE a AND (workspace_id IN ('1', '2') OR workspace_id IS NULL) "
        "AND b AND (u.workspace_id IN ('1', '2') OR u.workspace_id IS NULL)")
    assert render_workspace_filter(sql, None) == "WHERE a  AND b "


def test_every_system_extract_takes_the_workspace_filter():
    """Each system-table extract is limited to the chosen workspaces."""
    for ex in ADAPTER.extractors:
        for s in ex.sources:
            if hasattr(s, "sql") and "/system/" in str(s.sql):
                assert "{workspace_filter" in load_sql_path(s.sql), s.sql


def _spend_and_jobs(con):
    spend = con.execute("SELECT sum(usage_quantity) FROM raw.billing_usage_daily").fetchone()[0]
    jobs = con.execute("SELECT count(*) FROM raw.jobs").fetchone()[0]
    return spend, jobs


def test_workspace_filter_keeps_other_workspaces_out(tmp_path):
    """With one workspace chosen, spend and jobs from a second workspace are left out."""
    one = open_output(str(tmp_path / "one.duckdb"))
    run_collection(one, ADAPTER, FakeDatabricks(second_workspace=True, workspace_ids=("1",)),
                   profile=Profile.STANDARD)
    every = open_output(str(tmp_path / "all.duckdb"))
    run_collection(every, ADAPTER, FakeDatabricks(second_workspace=True, workspace_ids=None),
                   profile=Profile.STANDARD)
    spend_one, jobs_one = _spend_and_jobs(one)
    spend_all, jobs_all = _spend_and_jobs(every)
    assert spend_all - spend_one == pytest.approx(100.0)
    assert jobs_all - jobs_one == 1
    assert [r[0] for r in one.execute("SELECT workspace_id FROM raw.workspaces").fetchall()] == ["1"]


def test_report_says_which_workspaces_it_covers(out_db):
    """The report has a warning row naming the workspaces covered."""
    run_collection(out_db, ADAPTER, FakeDatabricks(workspace_ids=("1",)), profile=Profile.STANDARD)
    build_report(out_db)
    rows = out_db.execute(
        "SELECT detail FROM report.collection_warnings WHERE warning = 'workspace_scope'"
    ).fetchall()
    assert len(rows) == 1 and "lab (1)" in rows[0][0]
