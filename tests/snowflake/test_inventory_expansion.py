"""M3d (decision 18): inventory expansion from the Corrdyn review.

Boundaries pinned here: the read-heat aggregate never lands identities or
per-query rows (the narrow exception to decision 16 stays narrow), the RBAC
summary never lands the grant edge list or object names, and executable SQL
in SHOW output (dynamic-table definitions, alert conditions/actions) is
classified SOURCE_BODY so the handoff excludes it.
"""

from __future__ import annotations

from fixtures import REALISTIC, REALISTIC_SHOW

from md_migration_assessment.sources.snowflake.manifest import EXTRACTORS, Profile, load_sql
from md_migration_assessment.collect.runner import Scope, run_collection
from md_migration_assessment.handoff import _projection_columns
from md_migration_assessment.report import build_report

from fake_snowflake import FakeSource
from md_migration_assessment.sources.snowflake.manifest import (
    account_usage_sql,
    account_usage_view,
)
from md_migration_assessment.sources.snowflake import ADAPTER as SNOWFLAKE

M3D_NAMES = {
    "object_dependencies", "table_read_heat", "table_constraints",
    "referential_constraints", "sequences", "file_formats",
    "grants_to_roles_summary", "account_parameters", "network_policies",
    "storage_integrations", "notification_integrations", "api_integrations",
    "external_access_integrations", "external_volumes", "dynamic_tables",
    "dynamic_table_refresh_history", "alerts", "event_tables",
    "replication_groups", "failover_groups", "resource_monitors",
}
BY_NAME = {e.name: e for e in EXTRACTORS}


def realistic_source(**overrides) -> FakeSource:
    au = dict(REALISTIC)
    au.update(overrides)
    return FakeSource(
        account_usage=au, databases=["APPDB"], show_data=dict(REALISTIC_SHOW)
    )


def collect_and_report(out_db, profile=Profile.STANDARD, source=None):
    source = source or realistic_source()
    coll = run_collection(out_db, SNOWFLAKE, source, profile=profile)
    build_report(out_db)
    feats = {
        r[0]: dict(zip(("status", "count", "samples"), r[1:]))
        for r in out_db.execute(
            "SELECT feature, observation_status, count, sample_objects "
            "FROM report.feature_inventory WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    }
    return coll, feats, source


# ── manifest boundary ───────────────────────────────────────────────────


def test_m3d_extract_set_is_complete():
    assert M3D_NAMES <= set(BY_NAME)


def test_read_heat_is_aggregate_only_and_identity_free():
    """Decision 18's exception to decision 16 is NARROW: read heat is a
    server-side aggregate; user identities land only as a distinct count and
    nothing per-query (query ids, text) appears in the projection."""
    ex = BY_NAME["table_read_heat"]
    assert account_usage_view(ex) == "access_history"
    assert ex.min_edition == "ENTERPRISE"
    assert ex.window_from_history_days
    sql = load_sql("account_usage", account_usage_sql(ex))
    assert "GROUP BY" in sql
    cols = _projection_columns(sql)
    forbidden = {"user_name", "query_id", "query_text", "objects_modified"}
    assert not (cols & forbidden), cols & forbidden
    assert "count(DISTINCT ah.user_name)" in sql  # count, never names
    # write-method inference stays cut: the extract never reads
    # objects_modified
    assert "objects_modified" not in sql.lower()


def test_grants_summary_never_lands_the_edge_list():
    """Object names arrive only as counts; the raw grant edge list and
    per-object grants never land."""
    ex = BY_NAME["grants_to_roles_summary"]
    sql = load_sql("account_usage", account_usage_sql(ex))
    cols = _projection_columns(sql)
    assert cols == {
        "role_name", "granted_on", "n_grants", "n_privileges", "n_objects",
        "n_databases", "last_grant_created",
    }
    assert "GROUP BY" in sql


def test_executable_show_output_is_classified_source_body():
    from md_migration_assessment.privacy import PrivacyClass

    assert BY_NAME["dynamic_tables"].sensitive_fields["text"] is PrivacyClass.SOURCE_BODY
    assert BY_NAME["alerts"].sensitive_fields["condition"] is PrivacyClass.SOURCE_BODY
    assert BY_NAME["alerts"].sensitive_fields["action"] is PrivacyClass.SOURCE_BODY


def test_lite_capable_m3d_extracts():
    for name in ("table_constraints", "referential_constraints", "sequences",
                 "file_formats"):
        assert BY_NAME[name].min_profile is Profile.LITE, name


# ── collection + signals ────────────────────────────────────────────────


def test_m3d_extracts_complete_on_standard(out_db):
    coll, _, _ = collect_and_report(out_db)
    st = dict(out_db.execute(
        "SELECT extractor, status FROM meta.extract_runs WHERE collection_id = ?",
        [str(coll.collection_id)],
    ).fetchall())
    for name in M3D_NAMES:
        assert st[name] == "complete", (name, st[name])


def test_m3d_signals_observe_fixture_exemplars(out_db):
    _, feats, _ = collect_and_report(out_db)
    expected = {
        "object_dependencies": (1, "V_PLAIN"),      # SNOWFLAKE edge excluded
        "primary_key_constraints": (1, "PLAIN"),
        "unique_constraints": (1, "ORDERS"),
        "foreign_key_constraints": (1, "FK_ORDERS_PLAIN"),
        "sequences": (1, "ORDER_ID_SEQ"),
        "file_formats": (2, "PQ"),
        "xml_file_formats": (1, "LEGACY_XML"),
        "account_parameter_overrides": (1, "TIMEZONE"),
        "network_policies": (1, "CORP_ONLY"),
        "storage_integrations": (1, "S3_INT"),
        "notification_integrations": (1, "SNS_INT"),
        "api_integrations": (1, "LAMBDA_GW"),
        "external_access_integrations": (1, "OPENAI_EAI"),
        "external_volumes": (1, "ICEBERG_VOL"),
        "dynamic_table_refresh_activity": (1, "ORDERS_DYNAMIC"),
        "alerts": (1, "FRESHNESS_ALERT"),
        "event_tables": (1, "APP_EVENTS"),
        "replication_groups": (1, "RG1"),
        "failover_groups": (1, "FG1"),
        "resource_monitors": (1, "MONTHLY_CAP"),
        # ANALYST only: system roles excluded from the RBAC size number
        "roles_with_privilege_grants": (1, "ANALYST"),
    }
    for name, (n, sample_fragment) in expected.items():
        f = feats[name]
        assert f["status"] == "observed", (name, f)
        assert f["count"] == n, (name, f["count"])
        assert any(sample_fragment in s for s in f["samples"]), (name, f["samples"])


def test_lite_profile_covers_constraints_and_formats(out_db):
    coll, _, _ = collect_and_report(out_db, profile=Profile.LITE)
    rows = out_db.execute(
        "SELECT extractor, status, source_used FROM meta.extract_runs "
        "WHERE collection_id = ?",
        [str(coll.collection_id)],
    ).fetchall()
    st = {r[0]: (r[1], r[2]) for r in rows}
    for name in ("table_constraints", "referential_constraints", "sequences",
                 "file_formats"):
        assert st[name] == ("complete", "information_schema"), (name, st[name])
    # ACCOUNT_USAGE-only M3d extracts are visibly not requested under lite
    assert st["table_read_heat"][0] == "not_requested"
    assert st["grants_to_roles_summary"][0] == "not_requested"


def test_read_heat_scope_filters_server_side(out_db):
    source = realistic_source()
    run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB"])
    )
    rendered = [q for q in source.queries if "md-assess-extract: table_read_heat" in q]
    assert rendered
    assert (
        'split_part(o.value:"objectName"::VARCHAR, \'.\', 1) IN (\'APPDB\')'
        in rendered[0]
    )


def test_object_dependencies_scope_selects_referencing_side(out_db):
    source = realistic_source()
    run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB"])
    )
    rendered = [q for q in source.queries if "object_dependencies" in q]
    assert rendered
    assert "referencing_database IN ('APPDB')" in rendered[0]


# ── handoff boundary ────────────────────────────────────────────────────


def test_m3d_handoff_excludes_embedded_sql_and_travels_clean(out_db, tmp_path):
    from md_migration_assessment.handoff import build_handoff

    run_collection(out_db, SNOWFLAKE, realistic_source(), profile=Profile.STANDARD)
    build_report(out_db)
    src_path = str(tmp_path / "assessment.duckdb")
    out_db.close()
    manifest = build_handoff(src_path, str(tmp_path / "handoff.duckdb"))

    assert manifest["tables"]["raw.dynamic_tables"]["excluded_columns"] == ["text"]
    assert manifest["tables"]["raw.alerts"]["excluded_columns"] == ["action", "condition"]
    for name in M3D_NAMES - {"dynamic_tables", "alerts"}:
        entry = manifest["tables"][f"raw.{name}"]
        assert entry["dropped_unexpected"] == [], name
        assert entry["excluded_columns"] == [], name
