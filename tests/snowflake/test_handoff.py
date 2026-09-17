"""Privacy ship gate, behaviorally: the handoff builder must actually strip
source bodies while keeping names and coverage (spec §4)."""

from __future__ import annotations

import os
import stat

import duckdb
import pytest

from fake_snowflake import FakeSource

from fixtures import REALISTIC, REALISTIC_SHOW

from md_migration_assessment.sources.snowflake.manifest import Profile
from md_migration_assessment.collect.runner import run_collection
from md_migration_assessment.db import open_output
from md_migration_assessment.handoff import build_handoff
from md_migration_assessment.report import build_report
from md_migration_assessment.sources.snowflake.manifest import (
    account_usage_sql,
)
from md_migration_assessment.sources.snowflake import ADAPTER as SNOWFLAKE


@pytest.fixture()
def assessed_db(tmp_path):
    path = str(tmp_path / "private.duckdb")
    con = open_output(path)
    source = FakeSource(
        account_usage=dict(REALISTIC), databases=["APPDB"],
        show_data=dict(REALISTIC_SHOW),
    )
    run_collection(con, SNOWFLAKE, source, profile=Profile.STANDARD)
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


def test_handoff_rejects_motherduck_destinations(assessed_db):
    with pytest.raises(ValueError, match="local file path"):
        build_handoff(assessed_db, "md:sneaky_upload")
    with pytest.raises(ValueError, match="local file path"):
        build_handoff("md:remote_source", "/tmp/x.duckdb")


def test_handoff_drops_undeclared_drifted_columns(assessed_db, tmp_path):
    """Fail-closed: a column the version-controlled extract SQL never
    produced (drift, overrides) must not survive into a handoff."""
    con = duckdb.connect(assessed_db)
    con.execute("ALTER TABLE raw.views ADD COLUMN query_text VARCHAR")
    con.execute("UPDATE raw.views SET query_text = 'SELECT secret FROM t'")
    con.close()

    dest = str(tmp_path / "h.duckdb")
    manifest = build_handoff(assessed_db, dest)
    assert "query_text" in manifest["tables"]["raw.views"]["dropped_unexpected"]

    con = duckdb.connect(dest, read_only=True)
    try:
        assert "query_text" not in _cols(con, "raw", "views")
    finally:
        con.close()


def test_handoff_discloses_unclassified_included_columns(assessed_db, tmp_path):
    manifest = build_handoff(assessed_db, str(tmp_path / "h2.duckdb"))
    # every kept column is either classified-and-disclosed or listed as
    # unclassified — nothing travels invisibly
    for name, entry in manifest["tables"].items():
        if not name.startswith("raw."):
            continue
        disclosed = {c for cols in entry["sensitive_included"].values() for c in cols}
        listed = disclosed | set(entry["unclassified_included"])
        # every kept column is disclosed somewhere; a table may legitimately
        # list nothing only if everything non-framework was dropped as drift
        assert listed or entry["dropped_unexpected"], name


def test_handoff_multidot_destination_name(assessed_db, tmp_path):
    dest = str(tmp_path / "handoff.v1.duckdb")
    manifest = build_handoff(assessed_db, dest)
    con = duckdb.connect(dest, read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM raw.tables").fetchone()[0] > 0
    finally:
        con.close()
    assert manifest["tables"]["raw.tables"]["rows"] > 0


def test_handoff_drops_drifted_column_named_in_a_sql_comment(assessed_db, tmp_path):
    """Fail-closed even against comment words: views.sql *mentions*
    'source_body' in a comment, which must not whitelist a drifted column
    of that name (found in review, 2026-08-18)."""
    con = duckdb.connect(assessed_db)
    con.execute("ALTER TABLE raw.views ADD COLUMN source_body VARCHAR")
    con.execute("UPDATE raw.views SET source_body = 'SELECT secret FROM t'")
    con.close()

    dest = str(tmp_path / "h3.duckdb")
    manifest = build_handoff(assessed_db, dest)
    assert "source_body" in manifest["tables"]["raw.views"]["dropped_unexpected"]

    con = duckdb.connect(dest, read_only=True)
    try:
        assert "source_body" not in _cols(con, "raw", "views")
    finally:
        con.close()


def test_projection_parser_reads_aliases_not_keywords():
    from md_migration_assessment.sources.snowflake.manifest import EXTRACTORS, load_sql
    from md_migration_assessment.handoff import _projection_columns

    functions = next(e for e in EXTRACTORS if e.name == "functions")
    cols = _projection_columns(load_sql("account_usage", account_usage_sql(functions)))
    assert "is_secure" in cols       # CAST(NULL AS VARCHAR) AS is_secure → alias
    assert "varchar" not in cols     # type keyword must not leak into the allowlist
    assert "source_body" not in cols

    with pytest.raises(ValueError, match="SELECT"):
        _projection_columns("-- no select here")

    # commas inside function calls must not whitelist arguments
    cols = _projection_columns("SELECT IFF(p, database_name, q) AS db, other FROM t")
    assert cols == {"db", "other"}

    # function-level FROM must not truncate the projection
    cols = _projection_columns("SELECT EXTRACT(year FROM created) AS y, b FROM t")
    assert cols == {"y", "b"}

    # anything not bare-column or AS-aliased fails closed
    with pytest.raises(ValueError, match="unparseable"):
        _projection_columns("SELECT DISTINCT a FROM t")
    with pytest.raises(ValueError, match="unparseable"):
        _projection_columns("SELECT upper(a) FROM t")


@pytest.mark.parametrize("bad", ["md:x", "MD:x", "Md:x", "motherduck:x", "MOTHERDUCK:x", "s3://b/x.duckdb", "duckdb://x"])
def test_handoff_rejects_every_remote_scheme_spelling(assessed_db, tmp_path, bad):
    """DuckDB resolves 'motherduck:' and case variants of 'md:' to the cloud;
    the local-only rule is an allowlist (no scheme prefix), not a blocklist."""
    with pytest.raises(ValueError, match="local file path"):
        build_handoff(assessed_db, bad)
    with pytest.raises(ValueError, match="local file path|existing local file"):
        build_handoff(bad, str(tmp_path / "out.duckdb"))


def test_handoff_refuses_raw_schema_version_skew(assessed_db, tmp_path):
    """The expected-column allowlist comes from the installed extract SQL;
    on version skew it would silently classify real columns as drift."""
    con = duckdb.connect(assessed_db)
    con.execute("UPDATE meta.collections SET raw_schema_version = 1")
    con.close()
    with pytest.raises(ValueError, match="raw schema"):
        build_handoff(assessed_db, str(tmp_path / "h.duckdb"))


def test_manifest_drop_partitions_are_disjoint(assessed_db, tmp_path):
    con = duckdb.connect(assessed_db)
    con.execute("ALTER TABLE raw.views ADD COLUMN injected VARCHAR")
    con.close()
    manifest = build_handoff(assessed_db, str(tmp_path / "h4.duckdb"))
    for name, entry in manifest["tables"].items():
        if not name.startswith("raw."):
            continue
        excluded = set(entry["excluded_columns"])
        dropped = set(entry.get("dropped_unexpected", []))
        kept = set(entry["unclassified_included"]) | {
            c for cols in entry["sensitive_included"].values() for c in cols
        }
        assert not excluded & dropped, name
        assert not (excluded | dropped) & kept, name


def test_handoff_classifies_every_aggregate_string_column(assessed_db, tmp_path):
    """meta.* and report.* travel wholesale, so the identifiers they carry must be
    disclosed like raw columns are, and no string column may travel unclassified."""
    from md_migration_assessment.handoff import build_handoff

    manifest = build_handoff(assessed_db, str(tmp_path / "handoff.duckdb"))
    aggregate = {k: v for k, v in manifest["tables"].items() if not k.startswith("raw.")}
    assert aggregate, "no meta/report tables in the manifest"
    unclassified = {k: v["unclassified_included"] for k, v in aggregate.items() if v["unclassified_included"]}
    assert unclassified == {}, f"classify these in handoff.AGGREGATE_*_COLUMNS: {unclassified}"

    sizing = aggregate["report.sizing"]["sensitive_included"]
    assert sorted(sizing["object_name"]) == ["table_catalog", "table_name", "table_schema"]
    assert "sample_objects" in aggregate["report.feature_inventory"]["sensitive_included"]["object_name"]
    assert "source_deployment" in aggregate["meta.collections"]["sensitive_included"]["object_name"]
    assert "error_detail" in aggregate["meta.extract_runs"]["sensitive_included"]["comment"]
    # feature_inventory.note can carry "probe failed: <exception>" quoting object
    # names; the other note columns are fixed literals from the fact builders
    assert "note" in aggregate["report.feature_inventory"]["sensitive_included"]["comment"]
    for table in ("report.dialect_constructs", "report.ingestion_inventory", "report.tool_fingerprints"):
        assert "note" not in {c for cols in aggregate[table]["sensitive_included"].values() for c in cols}
    # each column is disclosed exactly once (the source and destination catalogs
    # both answer information_schema queries while the handoff is being built)
    for key, entry in aggregate.items():
        cols = [c for cs in entry["sensitive_included"].values() for c in cs] + entry["unclassified_included"]
        assert len(cols) == len(set(cols)), f"{key} lists a column twice: {cols}"
    # tool-generated labels are not disclosed as sensitive
    for entry in aggregate.values():
        for cols in entry["sensitive_included"].values():
            assert not ({"status", "observation_status", "tool_version"} & set(cols))
