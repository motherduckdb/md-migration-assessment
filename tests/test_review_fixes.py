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
    be silently absent from the inventory. (Empty as of M3a — the mechanism
    stays, and this test keeps it honest for future additions.)"""
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
        account_usage=dict(REALISTIC, tables=Exception(NOT_AUTHORIZED)),
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


def test_show_output_is_scope_filtered_client_side(out_db):
    """--scope is a privacy boundary: SHOW output rows for out-of-scope
    databases must not be persisted (found in review, 2026-08-18)."""
    import pyarrow as pa

    streams = pa.table({
        "name": ["IN_SCOPE", "OUT_SCOPE", "ACCOUNT_LEVEL"],
        "database_name": ["APPDB", "SECRETDB", None],
        "schema_name": ["S1", "S1", None],
    })
    source = FakeSource(
        account_usage=dict(REALISTIC),
        databases=["APPDB", "SECRETDB"],
        show_data={"streams": streams},
    )
    coll = run_collection(
        out_db, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB"])
    )
    names = {
        r[0] for r in out_db.execute("SELECT name FROM raw.streams").fetchall()
    }
    assert "OUT_SCOPE" not in names
    assert names == {"IN_SCOPE", "ACCOUNT_LEVEL"}  # NULL-db rows retained
    row = out_db.execute(
        "SELECT status, error_detail FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'streams'",
        [str(coll.collection_id)],
    ).fetchone()
    assert row[0] == "complete"
    assert "filtered client-side" in row[1]


def test_account_level_show_extracts_note_scope_inapplicability(out_db):
    source = FakeSource(account_usage=dict(REALISTIC), databases=["APPDB"])
    coll = run_collection(
        out_db, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB"])
    )
    row = out_db.execute(
        "SELECT status, error_detail FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'warehouses'",
        [str(coll.collection_id)],
    ).fetchone()
    assert row[0] == "complete"
    assert "no database residency" in row[1]


def test_show_scope_filter_handles_empty_results(out_db):
    """Regression (found live): filtering an empty SHOW result produced a
    null-typed take-index array that Arrow has no kernel for."""
    import pyarrow as pa

    empty = pa.table({
        "name": pa.array([], pa.string()),
        "database_name": pa.array([], pa.string()),
        "schema_name": pa.array([], pa.string()),
    })
    all_filtered = pa.table({
        "name": ["OUT_ONLY"],
        "database_name": ["SECRETDB"],
        "schema_name": ["S1"],
    })
    source = FakeSource(
        account_usage=dict(REALISTIC),
        databases=["APPDB", "SECRETDB"],
        show_data={"streamlit_apps": empty, "notebooks": all_filtered},
    )
    coll = run_collection(
        out_db, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB"])
    )
    statuses = dict(
        out_db.execute(
            "SELECT extractor, status FROM meta.extract_runs "
            "WHERE collection_id = ? AND extractor IN ('streamlit_apps', 'notebooks')",
            [str(coll.collection_id)],
        ).fetchall()
    )
    assert statuses == {"streamlit_apps": "complete", "notebooks": "complete"}
    assert out_db.execute("SELECT count(*) FROM raw.notebooks").fetchone()[0] == 0


def test_show_output_honors_schema_level_scope(out_db):
    """Confirmed repro from review: --scope APPDB.S1 must not persist rows
    from APPDB.SECRET_SCHEMA."""
    import pyarrow as pa

    streams = pa.table({
        "name": ["IN_SCHEMA", "OUT_SCHEMA", "NULL_SCHEMA", "ACCOUNT_LEVEL"],
        "database_name": ["APPDB", "APPDB", "APPDB", None],
        "schema_name": ["S1", "SECRET_SCHEMA", None, None],
    })
    source = FakeSource(
        account_usage=dict(REALISTIC),
        databases=["APPDB"],
        show_data={"streams": streams},
    )
    run_collection(
        out_db, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB.S1"])
    )
    names = {r[0] for r in out_db.execute("SELECT name FROM raw.streams").fetchall()}
    # schema-scoped: only the exact pair plus account-level NULL-db rows; a
    # NULL schema inside a schema-scoped database is unattributable → dropped
    assert names == {"IN_SCHEMA", "ACCOUNT_LEVEL"}


def test_show_scope_filter_fails_closed_on_missing_schema_column(out_db):
    import pyarrow as pa

    no_schema = pa.table({"name": ["X"], "database_name": ["APPDB"]})
    source = FakeSource(
        account_usage=dict(REALISTIC),
        databases=["APPDB"],
        show_data={"streams": no_schema},
    )
    coll = run_collection(
        out_db, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB.S1"])
    )
    row = out_db.execute(
        "SELECT status, error_detail FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'streams'",
        [str(coll.collection_id)],
    ).fetchone()
    assert row[0] == "failed"
    assert "schema_name" in row[1]


def test_database_only_show_extracts_keep_whole_database_under_schema_scope(out_db):
    """Confirmed repro from review: show_shares has no schema column; under
    --scope APPDB.S1 its APPDB rows must be kept (database granularity, like
    the SQL predicate), not dropped into a false observed_zero."""
    import pyarrow as pa

    shares = pa.table({
        "kind": ["OUTBOUND", "OUTBOUND", "INBOUND"],
        "name": ["APPDB_SHARE", "SECRET_SHARE", "NO_DB_YET"],
        "database_name": ["APPDB", "SECRETDB", None],
    })
    source = FakeSource(
        account_usage=dict(REALISTIC),
        databases=["APPDB", "SECRETDB"],
        show_data={"show_shares": shares},
    )
    coll = run_collection(
        out_db, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB.S1"])
    )
    names = {r[0] for r in out_db.execute("SELECT name FROM raw.show_shares").fetchall()}
    assert names == {"APPDB_SHARE", "NO_DB_YET"}
    row = out_db.execute(
        "SELECT actual_scope, error_detail FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'show_shares'",
        [str(coll.collection_id)],
    ).fetchone()
    assert row[0] == '["APPDB"]'  # achieved: coarser database granularity
    assert "database granularity" in row[1]


def test_scoped_show_extracts_record_actual_scope(out_db):
    source = FakeSource(
        account_usage=dict(REALISTIC),
        databases=["APPDB"],
        show_data=dict(__import__("fixtures").REALISTIC_SHOW),
    )
    coll = run_collection(
        out_db, source, profile=Profile.STANDARD, scope=Scope.parse(["APPDB.S1"])
    )
    row = out_db.execute(
        "SELECT actual_scope FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = 'streams'",
        [str(coll.collection_id)],
    ).fetchone()
    assert row[0] == '["APPDB.S1"]'  # schema-capable: exact requested scope
