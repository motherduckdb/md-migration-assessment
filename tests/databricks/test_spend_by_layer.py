"""report.spend_by_layer: DBUs and list cost per layer (ingestion, transformation,
BI serving, interactive, ML/AI, platform), billed by product map, with SQL
warehouse DBUs optionally split by write-statement elapsed share (estimated)."""

from __future__ import annotations

import uuid

import duckdb
import pytest

from md_migration_assessment.collect.extractor import Profile
from md_migration_assessment.collect.runner import run_collection
from md_migration_assessment.handoff import classify_aggregate_columns
from md_migration_assessment.report import build_report
from md_migration_assessment.sources.databricks import ADAPTER, facts
from md_migration_assessment.sources.databricks.facts import LAYER_OF_PRODUCT, _build_spend_by_layer

from fake_databricks import FakeDatabricks

CID = str(uuid.uuid4())

#: every billing_origin_product documented for system.billing.usage (Nov 2026)
DOCUMENTED = (
    "JOBS", "DLT", "SQL", "ALL_PURPOSE", "MODEL_SERVING", "INTERACTIVE", "DEFAULT_STORAGE",
    "VECTOR_SEARCH", "LAKEHOUSE_MONITORING", "PREDICTIVE_OPTIMIZATION", "ONLINE_TABLES",
    "FOUNDATION_MODEL_TRAINING", "AGENT_EVALUATION", "FINE_GRAINED_ACCESS_CONTROL",
    "EXTERNAL_COMPATIBILITY", "BASE_ENVIRONMENTS", "DATA_CLASSIFICATION",
    "DATA_QUALITY_MONITORING", "DATA_SHARING", "AI_GATEWAY", "AI_RUNTIME", "NETWORKING",
    "APPS", "DATABASE", "LAKEBASE", "AI_FUNCTIONS", "AGENT_BRICKS", "CLEAN_ROOM",
    "LAKEFLOW_CONNECT", "GENIE", "FEATURE_STORE", "SUPERVISOR_AGENT", "LAKEHOUSE_REAL_TIME",
    "DISASTER_RECOVERY",
)


def _con(billing, *, billing_status="complete", rollup=None, rollup_status="complete"):
    """In-memory db with raw billing (and optionally the query rollup) plus their runs."""
    con = duckdb.connect()
    con.execute("CREATE SCHEMA raw; CREATE SCHEMA meta")
    con.execute("CREATE TABLE meta.extract_runs (collection_id UUID, extractor VARCHAR, status VARCHAR)")
    con.execute("""CREATE TABLE raw.billing_usage_daily (collection_id UUID, usage_date DATE,
        billing_origin_product VARCHAR, sku_name VARCHAR, usage_unit VARCHAR, usage_type VARCHAR,
        warehouse_id VARCHAR, is_serverless BOOLEAN, usage_quantity DOUBLE, list_cost DOUBLE)""")
    for product, wh, dbus, cost, *unit in billing:
        con.execute(
            "INSERT INTO raw.billing_usage_daily VALUES (?, DATE '2029-12-30', ?, 'SKU', ?, "
            "'COMPUTE_TIME', ?, true, ?, ?)",
            [CID, product, unit[0] if unit else "DBU", wh, dbus, cost],
        )
    con.execute("INSERT INTO meta.extract_runs VALUES (?, 'billing_usage_daily', ?)", [CID, billing_status])
    if rollup is not None:
        con.execute("""CREATE TABLE raw.query_workload_rollup (collection_id UUID, warehouse_id VARCHAR,
            statement_type VARCHAR, usage_date DATE, sum_duration_ms BIGINT)""")
        for wh, stmt, ms in rollup:
            con.execute("INSERT INTO raw.query_workload_rollup VALUES (?, ?, ?, DATE '2029-12-30', ?)",
                        [CID, wh, stmt, ms])
        con.execute("INSERT INTO meta.extract_runs VALUES (?, 'query_workload_rollup', ?)", [CID, rollup_status])
    return con


def _rows(con):
    return con.execute("""
        SELECT layer, billing_origin_product, round(dbus, 6), round(list_cost, 6), method,
               extract_status, note
        FROM report.spend_by_layer ORDER BY ALL""").fetchall()


def test_every_documented_product_has_a_layer():
    """The map covers the documented products, and each lands in exactly one layer."""
    assert set(DOCUMENTED) <= set(LAYER_OF_PRODUCT)
    assert set(LAYER_OF_PRODUCT.values()) == {
        "ingestion", "transformation", "bi_serving", "interactive", "ml_ai", "platform"}


@pytest.mark.parametrize("product,layer", [
    ("DLT", "ingestion"), ("LAKEFLOW_CONNECT", "ingestion"), ("JOBS", "transformation"),
    ("SQL", "bi_serving"), ("ALL_PURPOSE", "interactive"), ("INTERACTIVE", "interactive"),
    ("NOTEBOOKS", "interactive"), ("MODEL_SERVING", "ml_ai"), ("VECTOR_SEARCH", "ml_ai"),
    ("AI_FUNCTIONS", "ml_ai"), ("GENIE", "ml_ai"), ("PREDICTIVE_OPTIMIZATION", "platform"),
    ("LAKEHOUSE_MONITORING", "platform"),
])
def test_product_maps_to_layer_as_billed(product, layer):
    con = _con([(product, None, 2.0, 1.4)])
    _build_spend_by_layer(con)
    rows = _rows(con)
    assert [(r[0], r[1], r[2], r[3], r[4]) for r in rows] == [(layer, product, 2.0, 1.4, "billed")]


def test_unknown_product_falls_to_platform_with_a_note():
    con = _con([("SOMETHING_NEW", None, 3.0, None), (None, None, 1.0, None)])
    _build_spend_by_layer(con)
    rows = _rows(con)
    assert {(r[0], r[1]) for r in rows} == {("platform", "SOMETHING_NEW"), ("platform", "UNKNOWN")}
    assert all(r[6] == facts._UNMAPPED_NOTE for r in rows)
    # known platform products carry no "unmapped" note
    con = _con([("PREDICTIVE_OPTIMIZATION", None, 1.0, 0.1)])
    _build_spend_by_layer(con)
    assert _rows(con)[0][6] is None


def test_non_dbu_units_are_left_out():
    con = _con([("DEFAULT_STORAGE", None, 7.0, None, "GB_MONTH"), ("SQL", "wh1", 1.0, 0.7)])
    _build_spend_by_layer(con)
    assert [r[1] for r in _rows(con)] == ["SQL"]


def test_partial_billing_status_carries_through():
    con = _con([("JOBS", None, 1.0, 0.3)], billing_status="partial")
    _build_spend_by_layer(con)
    assert _rows(con)[0][5] == "partial"


def test_missing_billing_extract_leaves_an_empty_table():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA meta; CREATE TABLE meta.extract_runs (collection_id UUID, extractor VARCHAR, status VARCHAR)")
    _build_spend_by_layer(con)
    assert con.execute("SELECT count(*) FROM report.spend_by_layer").fetchone()[0] == 0


def test_sql_warehouse_split_by_write_share_is_estimated():
    """wh1 spent 25% of elapsed time in writes: 25% of its DBUs move to transformation."""
    con = _con(
        [("SQL", "wh1", 8.0, 4.0), ("SQL", "wh2", 2.0, 1.0), ("JOBS", None, 1.0, 0.5)],
        rollup=[("wh1", "SELECT", 300), ("wh1", "MERGE", 50), ("wh1", "INSERT", 50)],
    )
    _build_spend_by_layer(con)
    got = {(r[0], r[1], r[4]): (r[2], r[3]) for r in _rows(con)}
    assert got == {
        ("bi_serving", "SQL", "estimated"): (6.0, 3.0),
        ("transformation", "SQL", "estimated"): (2.0, 1.0),
        # no rollup for wh2: the billed map stands
        ("bi_serving", "SQL", "billed"): (2.0, 1.0),
        ("transformation", "JOBS", "billed"): (1.0, 0.5),
    }
    # the split never changes the total
    assert con.execute("SELECT sum(dbus) FROM report.spend_by_layer").fetchone()[0] == pytest.approx(11.0)


def test_no_split_from_a_partial_rollup_or_a_read_only_warehouse():
    con = _con([("SQL", "wh1", 8.0, 4.0)], rollup=[("wh1", "MERGE", 50)], rollup_status="partial")
    _build_spend_by_layer(con)
    assert [(r[0], r[4]) for r in _rows(con)] == [("bi_serving", "billed")]
    con = _con([("SQL", "wh1", 8.0, 4.0)], rollup=[("wh1", "SELECT", 50)])
    _build_spend_by_layer(con)
    assert [(r[0], r[4]) for r in _rows(con)] == [("bi_serving", "billed")]


def test_all_write_warehouse_has_no_empty_bi_row():
    con = _con([("SQL", "wh1", 8.0, 4.0)], rollup=[("wh1", "COPY", 50)])
    _build_spend_by_layer(con)
    assert [(r[0], r[2], r[4]) for r in _rows(con)] == [("transformation", 8.0, "estimated")]


def test_collection_builds_spend_by_layer_and_classifies_for_handoff(out_db):
    """End to end through the fake workspace; every string column is classified."""
    run_collection(out_db, ADAPTER, FakeDatabricks(), profile=Profile.STANDARD)
    build_report(out_db)
    rows = out_db.execute(
        "SELECT layer, billing_origin_product, method FROM report.spend_by_layer ORDER BY ALL").fetchall()
    assert ("transformation", "JOBS", "billed") in rows
    assert any(r[1] == "SQL" and r[0] == "bi_serving" for r in rows)
    cols = [r[0] for r in out_db.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'report' "
        "AND table_name = 'spend_by_layer' AND data_type IN ('VARCHAR', 'UUID')").fetchall()]
    disclosed, unclassified = classify_aggregate_columns("report.spend_by_layer", cols)
    assert unclassified == [] and disclosed == {}
