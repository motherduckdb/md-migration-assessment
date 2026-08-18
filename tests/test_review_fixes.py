"""Regression tests for the 2026-08-18 code-review findings."""

from __future__ import annotations

import os
import stat

import pytest

from conftest import NOT_AUTHORIZED, FakeSource

from fixtures import REALISTIC

from md_migration_assessment.collect.manifest import Profile
from md_migration_assessment.collect.runner import Scope, run_collection
from md_migration_assessment.db import open_output
from md_migration_assessment.report import build_report
from md_migration_assessment.report.signals import PLANNED_SIGNALS


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_wal_sidecar_is_private_while_connection_is_live(tmp_path):
    """P1: the 0600 guarantee must cover the DuckDB WAL, which is created
    lazily by writes after open_output chmods the main file."""
    os.umask(0o022)  # hostile default; open_output must override it
    path = str(tmp_path / "a.duckdb")
    con = open_output(path)
    # keep the WAL alive: raise the checkpoint threshold, then write
    con.execute("SET checkpoint_threshold = '10GB'")
    con.execute("CREATE TABLE raw.t AS SELECT range AS i FROM range(1000)")
    wal = path + ".wal"
    assert os.path.exists(wal), "expected a live WAL for this test"
    assert _mode(wal) == 0o600, oct(_mode(wal))
    assert _mode(path) == 0o600
    con.close()


def test_second_collection_in_same_file_is_rejected(out_db):
    """P1: CREATE OR REPLACE ingestion would silently destroy the first
    collection's raw evidence while meta.collections still listed both."""
    source = FakeSource(account_usage=dict(REALISTIC), databases=["APPDB"])
    run_collection(out_db, source, profile=Profile.STANDARD)
    with pytest.raises(ValueError, match="already contains a collection"):
        run_collection(out_db, source, profile=Profile.STANDARD)


def test_scoped_database_not_visible_degrades_to_partial(out_db):
    """P1: a requested database the role cannot enumerate is missing
    evidence — never silently dropped from scope."""
    source = FakeSource(databases=["VISIBLE"])
    scope = Scope.parse(["VISIBLE", "HIDDEN"])
    coll = run_collection(out_db, source, profile=Profile.LITE, scope=scope)
    row = out_db.execute(
        "SELECT status, actual_scope, error_detail FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'tables'",
        [str(coll.collection_id)],
    ).fetchone()
    assert row[0] == "partial"
    assert "VISIBLE" in row[1] and "HIDDEN" not in row[1]
    assert "HIDDEN" in row[2] and "not visible" in row[2]


def test_scope_entirely_invisible_is_unavailable(out_db):
    source = FakeSource(databases=["VISIBLE"])
    scope = Scope.parse(["HIDDEN"])
    coll = run_collection(out_db, source, profile=Profile.LITE, scope=scope)
    status = out_db.execute(
        "SELECT status FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'tables'",
        [str(coll.collection_id)],
    ).fetchone()[0]
    assert status == "unavailable"


def test_quoted_database_names_are_walked_not_rejected(out_db):
    """P2: Snowflake permits identifiers with spaces/punctuation; discovered
    names must be quoted, not failed against the scope regex."""
    source = FakeSource(databases=["My DB", "USER$WEIRD.NAME"])
    coll = run_collection(out_db, source, profile=Profile.LITE)
    row = out_db.execute(
        "SELECT status, actual_scope FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'tables'",
        [str(coll.collection_id)],
    ).fetchone()
    assert row[0] == "complete", row
    assert "My DB" in row[1]
    walked = [q for q in source.queries if "information_schema.tables" in q]
    assert any('"My DB".information_schema' in q for q in walked)


def test_planned_signals_are_visible_unknowns(out_db):
    """P1: unimplemented taxonomy entries must appear as unknown rows, never
    be silently absent from the inventory."""
    source = FakeSource(account_usage=dict(REALISTIC), databases=["APPDB"])
    run_collection(out_db, source, profile=Profile.STANDARD)
    build_report(out_db)
    rows = dict(
        out_db.execute(
            "SELECT feature, note FROM report.feature_inventory "
            "WHERE observation_status = 'unknown' AND source_extractor = '(not implemented)'"
        ).fetchall()
    )
    assert {p.name for p in PLANNED_SIGNALS} == set(rows)
    assert "SHOW STREAMS" in rows["streams"]


def test_sizing_carries_coverage_status(out_db):
    source = FakeSource(
        account_usage=dict(REALISTIC, table_storage_metrics=Exception(NOT_AUTHORIZED)),
        databases=["APPDB"],
    )
    run_collection(out_db, source, profile=Profile.STANDARD)
    build_report(out_db)
    row = out_db.execute(
        "SELECT tables_extract_status, storage_extract_status, active_bytes "
        "FROM report.sizing WHERE table_name = 'PLAIN'"
    ).fetchone()
    assert row[0] == "complete"
    assert row[1] == "unavailable"  # nulls below are explained, not implied
    assert row[2] is None


def test_sizing_relation_exists_even_without_table_evidence(out_db):
    """P2: an entirely invisible scope must yield an empty sizing relation
    with coverage recorded in meta — not 'relation does not exist'."""
    source = FakeSource(
        account_usage={"tables": Exception(NOT_AUTHORIZED)},
        info_schema={"tables": {"APPDB": Exception(NOT_AUTHORIZED)}},
        databases=["APPDB"],
    )
    run_collection(out_db, source, profile=Profile.STANDARD)
    # raw.tables was never created
    assert not out_db.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='raw' AND table_name='tables'"
    ).fetchone()[0]
    build_report(out_db)
    n = out_db.execute("SELECT count(*) FROM report.sizing").fetchone()[0]
    assert n == 0
