"""Report layer: signal correctness and the observation-status contract."""

from __future__ import annotations

import pytest

from fake_snowflake import NOT_AUTHORIZED, FakeSource

from fixtures import REALISTIC, REALISTIC_SHOW

from md_migration_assessment.sources.snowflake.manifest import Profile
from md_migration_assessment.collect.runner import run_collection
from md_migration_assessment.report import build_report
from md_migration_assessment.sources.snowflake.signals import PLANNED_SIGNALS, SIGNALS
from md_migration_assessment.sources.snowflake import ADAPTER as SNOWFLAKE


def collect_and_report(out_db, source, profile=Profile.STANDARD):
    coll = run_collection(out_db, SNOWFLAKE, source, profile=profile)
    build_report(out_db)
    rows = out_db.execute(
        """
        SELECT feature, observation_status, count, sample_objects, note
        FROM report.feature_inventory WHERE collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchall()
    return coll, {
        r[0]: dict(zip(("status", "count", "samples", "note"), r[1:])) for r in rows
    }


def realistic_source(**overrides):
    au = dict(REALISTIC)
    au.update(overrides)
    return FakeSource(
        account_usage=au, databases=["APPDB"], show_data=dict(REALISTIC_SHOW)
    )


def test_every_signal_has_a_row(out_db):
    _, feats = collect_and_report(out_db, realistic_source())
    expected = {s.name for s in SIGNALS} | {p.name for p in PLANNED_SIGNALS}
    assert set(feats) == expected


@pytest.mark.parametrize(
    ("feature", "expected", "sample_contains"),
    [
        ("transient_tables", 1, "TRANSIENT_T"),
        ("iceberg_tables", 1, "ICEBERG_T"),
        ("dynamic_tables", 1, "DYNAMIC_T"),
        ("hybrid_tables", 1, "HYBRID_T"),
        ("materialized_views", 1, "MV_T"),
        ("clustered_tables_by_column", 1, "CLUSTERED_COL"),
        ("clustered_tables_by_expression", 1, "CLUSTERED_EXPR"),
        ("auto_clustering_enabled", 1, "CLUSTERED_EXPR"),
        ("extended_time_travel_8_14d", 1, "TT_10D"),
        ("extended_time_travel_gt_14d", 1, "TT_30D"),
        ("zero_copy_clones", 1, "CLONE_T"),
        ("variant_columns", 1, "PAYLOAD"),
        ("object_columns", 1, "META"),
        ("array_columns", 1, "TAGS_A"),
        ("geospatial_columns", 1, "LOC"),
        ("vector_columns", 1, "EMB"),
        ("timestamp_tz_columns", 1, "TS_TZ"),
        ("timestamp_ltz_columns", 1, "TS_LTZ"),
        ("nanosecond_timestamp_columns", 1, "TS_NANO"),
        ("javascript_udfs", 1, "F_JS"),
        ("python_udfs", 1, "F_PY"),
        ("java_scala_udfs", 1, "F_EXT"),
        ("external_functions", 1, "F_EXT"),
        ("snowpark_udfs", 1, "F_PY"),
        ("stored_procedures", 4, "P_SQL"),
        ("javascript_procedures", 1, "P_JS"),
        ("python_procedures", 1, "P_PY"),
        ("secure_views", 1, "V_SECURE"),
        ("masking_policies", 1, "MASK_EMAIL"),
        ("row_access_policies", 1, "RAP_REGION"),
        ("masking_protected_columns", 1, "EMAIL"),
        ("row_access_protected_objects", 1, "PLAIN"),
        ("tags", 1, "PII"),
        ("tag_assignments", 1, "PLAIN"),
        ("custom_roles", 1, "ANALYST"),
        ("snowpipes", 2, "LOAD_EVENTS"),
        ("outbound_shares", 1, "OUT_SHARE"),
        ("external_tables", 1, "EXT_EVENTS"),
        ("cursors_in_procedures", 1, "P_CURSOR"),
        ("streams", 1, "ORDERS_STREAM"),
        ("warehouses", 2, "ETL_WH"),
        ("multi_cluster_warehouses", 1, "ETL_WH"),
        ("streamlit_apps", 1, "SALES_DASH"),
        ("notebooks", 1, "EDA_NOTEBOOK"),
        ("native_apps", 1, "PARTNER_APP"),
        ("native_app_packages", 1, "MY_PKG"),
        ("catalog_integrations", 1, "GLUE_CAT"),
        ("inbound_shares", 1, "PARTNERORG.SHARE1"),
        ("cortex_ai_usage", 1, "COMPLETE"),
        ("search_optimization", 1, "PLAIN"),
        ("snowpipe_streaming", 1, "KAFKA_CONNECTOR_1"),
        ("listings", 1, "PARTNER_SHARE"),
        ("scheduled_tasks", 1, "NIGHTLY_ROLLUP"),
        ("external_stages", 1, "S3_LANDING"),
        ("auto_ingest_pipes", 1, "LOAD_EVENTS"),
    ],
)
def test_signal_counts(out_db, feature, expected, sample_contains):
    _, feats = collect_and_report(out_db, realistic_source())
    row = feats[feature]
    assert row["status"] == "observed", (feature, row)
    assert row["count"] == expected, (feature, row)
    assert any(sample_contains in s for s in row["samples"]), (feature, row)


def test_system_objects_excluded_from_counts(out_db):
    """The SNOWFLAKE-catalog transient table and VARIANT column exist in raw
    but must not inflate feature counts."""
    _, feats = collect_and_report(out_db, realistic_source())
    assert feats["transient_tables"]["count"] == 1
    assert feats["variant_columns"]["count"] == 1
    n_raw = out_db.execute(
        "SELECT count(*) FROM raw.tables WHERE table_catalog = 'SNOWFLAKE'"
    ).fetchone()[0]
    assert n_raw == 1  # evidence retained, judgment-free


def test_unavailable_source_is_unknown_never_zero(out_db):
    source = realistic_source(masking_policies=Exception(NOT_AUTHORIZED))
    _, feats = collect_and_report(out_db, source)
    row = feats["masking_policies"]
    assert row["status"] == "unknown"
    assert row["count"] is None
    assert "unavailable" in row["note"]


def test_not_requested_propagates_in_lite(out_db):
    _, feats = collect_and_report(out_db, realistic_source(), profile=Profile.LITE)
    # standard-only source → not_requested
    assert feats["masking_policies"]["status"] == "not_requested"
    assert feats["zero_copy_clones"]["status"] == "not_requested"
    # lite-collectable source → observed via INFORMATION_SCHEMA
    assert feats["transient_tables"]["status"] == "observed"


def test_partial_nonzero_is_lower_bound(out_db):
    source = FakeSource(
        account_usage=dict(REALISTIC, tables=Exception(NOT_AUTHORIZED)),
        info_schema={
            "tables": {
                "APPDB": REALISTIC["tables"],
                "DB2": Exception(NOT_AUTHORIZED),
            }
        },
        databases=["APPDB", "DB2"],
    )
    _, feats = collect_and_report(out_db, source)

    assert feats["transient_tables"]["status"] == "observed"
    assert "lower bound" in feats["transient_tables"]["note"]


def test_partial_zero_is_unknown_not_zero(out_db):
    """A signal with no hits under partial coverage must be unknown."""
    plain_only = REALISTIC["tables"].filter(
        __import__("pyarrow").compute.equal(REALISTIC["tables"]["table_name"], "PLAIN")
    )
    source = FakeSource(
        account_usage=dict(REALISTIC, tables=Exception(NOT_AUTHORIZED)),
        info_schema={
            "tables": {"APPDB": plain_only, "DB2": Exception(NOT_AUTHORIZED)}
        },
        databases=["APPDB", "DB2"],
    )
    _, feats = collect_and_report(out_db, source)
    row = feats["dynamic_tables"]
    assert row["status"] == "unknown"
    assert row["count"] is None
    assert "partial" in row["note"]


def test_probe_error_is_unknown_with_note(out_db):
    # default small_table lacks the roles columns → probe fails → unknown
    au = {k: v for k, v in REALISTIC.items() if k != "roles"}
    _, feats = collect_and_report(
        out_db, FakeSource(account_usage=au, databases=["APPDB"])
    )
    row = feats["custom_roles"]
    assert row["status"] == "unknown"
    assert "probe failed" in row["note"]


def test_observed_zero_requires_complete_coverage(out_db):
    """An empty-but-complete extract is observed_zero — distinct from unknown."""
    empty_pipes = REALISTIC["pipes"].schema.empty_table()
    _, feats = collect_and_report(out_db, realistic_source(pipes=empty_pipes))
    assert feats["snowpipes"]["status"] == "observed_zero"
    assert feats["snowpipes"]["count"] == 0


def test_rebuild_is_idempotent(out_db):
    collect_and_report(out_db, realistic_source())
    build_report(out_db)
    n = out_db.execute("SELECT count(*) FROM report.feature_inventory").fetchone()[0]
    assert n == len(SIGNALS) + len(PLANNED_SIGNALS)


def test_raw_schema_version_mismatch_is_a_clear_error(out_db):
    coll, _ = collect_and_report(out_db, realistic_source())
    out_db.execute(
        "UPDATE meta.collections SET raw_schema_version = 1 WHERE collection_id = ?",
        [str(coll.collection_id)],
    )
    with pytest.raises(ValueError, match="raw schema"):
        build_report(out_db)


def test_sizing_built_with_system_flag_and_storage_join(out_db):
    collect_and_report(out_db, realistic_source())
    rows = out_db.execute(
        "SELECT table_name, is_system, active_bytes FROM report.sizing ORDER BY table_name"
    ).fetchall()
    by_name = {r[0]: r for r in rows}
    assert by_name["SYS_T"][1] is True
    assert by_name["PLAIN"][1] is False
    assert by_name["PLAIN"][2] == 1000  # storage metrics joined
    assert "MV_T" in by_name  # materialized views included in sizing
