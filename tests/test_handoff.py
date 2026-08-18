"""Privacy ship gate, behaviorally: the handoff builder must actually strip
source bodies while keeping names and coverage (spec §4)."""

from __future__ import annotations

import os
import stat

import duckdb
import pytest

from conftest import FakeSource

from fixtures import REALISTIC

from md_migration_assessment.collect.manifest import Profile
from md_migration_assessment.collect.runner import run_collection
from md_migration_assessment.db import open_output
from md_migration_assessment.handoff import build_handoff
from md_migration_assessment.report import build_report


@pytest.fixture()
def assessed_db(tmp_path):
    path = str(tmp_path / "private.duckdb")
    con = open_output(path)
    source = FakeSource(account_usage=dict(REALISTIC), databases=["APPDB"])
    run_collection(con, source, profile=Profile.STANDARD)
    build_report(con)
    con.close()
    return path


def _cols(con, schema, table):
    return {
        r[0].lower()
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, table],
        ).fetchall()
    }


def test_handoff_strips_every_source_body(assessed_db, tmp_path):
    dest = str(tmp_path / "handoff.duckdb")
    manifest = build_handoff(assessed_db, dest)

    con = duckdb.connect(dest, read_only=True)
    try:
        for table, column in [
            ("views", "view_definition"),
            ("functions", "function_definition"),
            ("procedures", "procedure_definition"),
            ("masking_policies", "policy_body"),
            ("row_access_policies", "policy_body"),
            ("pipes", "definition"),
            ("tasks", "definition"),
            ("tasks", "condition"),
            ("columns", "column_default"),
        ]:
            assert column not in _cols(con, "raw", table), (table, column)
            assert column in manifest["tables"][f"raw.{table}"]["excluded_columns"]

        # object names and evidence rows survive
        n = con.execute(
            "SELECT count(*) FROM raw.views WHERE table_name = 'V_SECURE'"
        ).fetchone()[0]
        assert n == 1
        # facts and coverage travel wholesale
        assert con.execute("SELECT count(*) FROM report.feature_inventory").fetchone()[0] > 0
        assert con.execute("SELECT count(*) FROM meta.extract_runs").fetchone()[0] > 0
    finally:
        con.close()

    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600


def test_handoff_discloses_included_sensitive_classes(assessed_db, tmp_path):
    manifest = build_handoff(assessed_db, str(tmp_path / "h.duckdb"))
    disclosed = manifest["tables"]["raw.tables"]["sensitive_included"]
    assert "table_name" in disclosed["object_name"]
    assert "table_owner" in disclosed["user_identity"]


def test_handoff_refuses_to_overwrite(assessed_db, tmp_path):
    dest = str(tmp_path / "h.duckdb")
    build_handoff(assessed_db, dest)
    with pytest.raises(ValueError, match="refusing to overwrite"):
        build_handoff(assessed_db, dest)
