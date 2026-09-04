"""Runner behavior: statuses, fallbacks, scope, and the never-zero contract."""

from __future__ import annotations

import pyarrow as pa

from fake_snowflake import NOT_AUTHORIZED, FakeSource, small_table

from md_migration_assessment.sources.snowflake.manifest import EXTRACTORS, Profile
from md_migration_assessment.collect.runner import Scope, run_collection
from md_migration_assessment.sources.snowflake import ADAPTER as SNOWFLAKE


def statuses(con, coll):
    return dict(
        con.execute(
            "SELECT extractor, status FROM meta.extract_runs WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    )


def run_row(con, coll, extractor):
    cols = [
        "status", "source_used", "actual_scope", "rows_written",
        "error_category", "error_detail", "retryable",
    ]
    row = con.execute(
        f"SELECT {', '.join(cols)} FROM meta.extract_runs "
        "WHERE collection_id = ? AND extractor = ?",
        [str(coll.collection_id), extractor],
    ).fetchone()
    return dict(zip(cols, row))


def test_standard_happy_path(out_db):
    source = FakeSource()
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)

    st = statuses(out_db, coll)
    # standard requests every extractor (decision 17 folded 'full' in)
    assert set(st.values()) == {"complete"}
    assert len(st) == len(EXTRACTORS)

    # every raw table exists and is stamped with the collection id
    for name in ("tables", "views", "table_storage_metrics"):
        n = out_db.execute(
            f"SELECT count(*) FROM raw.{name} WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchone()[0]
        assert n > 0

    finished = out_db.execute(
        "SELECT finished_at FROM meta.collections WHERE collection_id = ?",
        [str(coll.collection_id)],
    ).fetchone()[0]
    assert finished is not None

    # collection identity captured session info
    account = out_db.execute("SELECT source_deployment FROM meta.collections").fetchone()[0]
    assert account == "TESTACCT"


def test_account_usage_denied_falls_back_to_information_schema(out_db):
    source = FakeSource(account_usage={"tables": Exception(NOT_AUTHORIZED)})
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)

    row = run_row(out_db, coll, "tables")
    assert row["status"] == "complete"
    assert row["source_used"] == "information_schema"
    assert "fallback" in row["error_detail"]
    # walked both fake databases
    assert '"DB1"' in row["actual_scope"] and '"DB2"' in row["actual_scope"]


def test_partial_fallback_records_actual_coverage(out_db):
    source = FakeSource(
        account_usage={"tables": Exception(NOT_AUTHORIZED)},
        info_schema={"tables": {"DB2": Exception(NOT_AUTHORIZED)}},
    )
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)

    row = run_row(out_db, coll, "tables")
    assert row["status"] == "partial"
    assert '"DB1"' in row["actual_scope"] and '"DB2"' not in row["actual_scope"]
    assert row["retryable"] is True
    assert "DB2" in row["error_detail"]

    gaps = {
        r[0]
        for r in out_db.execute("SELECT extractor FROM meta.gaps").fetchall()
    }
    assert "tables" in gaps


def test_all_fallback_databases_denied_is_unavailable(out_db):
    source = FakeSource(
        account_usage={"views": Exception(NOT_AUTHORIZED)},
        info_schema={"views": {"DB1": Exception(NOT_AUTHORIZED), "DB2": Exception(NOT_AUTHORIZED)}},
    )
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)
    row = run_row(out_db, coll, "views")
    assert row["status"] == "unavailable"
    assert row["error_category"] == "privilege"


def test_unexpected_error_is_failed_and_does_not_fall_back(out_db):
    source = FakeSource(account_usage={"columns": Exception("network timeout")})
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)

    row = run_row(out_db, coll, "columns")
    assert row["status"] == "failed"
    assert row["retryable"] is True
    # no INFORMATION_SCHEMA query was attempted for columns
    assert not any("information_schema.columns" in q for q in source.queries)


def test_no_fallback_extract_denied_is_unavailable(out_db):
    source = FakeSource(account_usage={"table_storage_metrics": Exception(NOT_AUTHORIZED)})
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)
    row = run_row(out_db, coll, "table_storage_metrics")
    assert row["status"] == "unavailable"
    # never presented as an observed zero: no raw table was created
    exists = out_db.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='raw' AND table_name='table_storage_metrics'"
    ).fetchone()[0]
    assert exists == 0


def test_lite_profile_never_touches_account_usage(out_db):
    source = FakeSource()
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.LITE)

    assert not any("account_usage" in q for q in source.queries)
    st = statuses(out_db, coll)
    assert st["tables"] == "complete"
    assert st["table_storage_metrics"] == "not_requested"
    assert st["stage_storage_usage_history"] == "not_requested"


def test_scope_filters_account_usage_and_fallback_walk(out_db):
    source = FakeSource(
        account_usage={"tables": Exception(NOT_AUTHORIZED)},
        databases=["SALES", "OTHER"],
    )
    scope = Scope.parse(["sales", "analytics.reporting"])
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD, scope=scope)

    au = [q for q in source.queries if "account_usage.columns" in q][0]
    assert "table_catalog IN ('ANALYTICS', 'SALES')" in au or "'SALES'" in au
    assert "ANALYTICS" in au

    # fallback walked only scoped databases (SALES; ANALYTICS not in account)
    walked = [q for q in source.queries if "information_schema.tables" in q]
    assert walked and all("OTHER." not in q for q in walked)

    row = run_row(out_db, coll, "tables")
    assert row["actual_scope"] == '["SALES"]'


def test_empty_result_is_observed_zero_with_complete_status(out_db):
    empty = pa.table({"table_catalog": pa.array([], pa.string())})
    source = FakeSource(account_usage={"tables": empty})
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)
    row = run_row(out_db, coll, "tables")
    assert row["status"] == "complete"
    assert row["rows_written"] == 0
    # the raw table exists (observed zero), distinguishing it from unavailable
    n = out_db.execute("SELECT count(*) FROM raw.tables").fetchone()[0]
    assert n == 0


def test_scope_rejects_bad_identifiers():
    import pytest

    with pytest.raises(ValueError):
        Scope.parse(["bad-name"])
    with pytest.raises(ValueError):
        Scope.parse(["db.schema.table"])
    with pytest.raises(ValueError):
        Scope.parse(["db'; drop table x--"])


def test_fallback_nonprivilege_error_is_failed_not_unavailable(out_db):
    """A broken ingest (e.g. an Arrow type error) must never masquerade as a
    permissions gap — found live when connector Tables broke DuckDB's scan."""
    source = FakeSource(
        account_usage={"tables": Exception(NOT_AUTHORIZED)},
        info_schema={
            "tables": {
                "DB1": Exception("Invalid Input Error: arrow_scan: get_next failed()"),
                "DB2": Exception("Invalid Input Error: arrow_scan: get_next failed()"),
            }
        },
    )
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)
    row = run_row(out_db, coll, "tables")
    assert row["status"] == "failed"
    assert row["error_category"] == "error"
    assert row["retryable"] is True


def test_fallback_mixed_error_kinds_partial_is_error_category(out_db):
    source = FakeSource(
        account_usage={"tables": Exception(NOT_AUTHORIZED)},
        info_schema={"tables": {"DB2": Exception("arrow_scan: get_next failed()")}},
    )
    coll = run_collection(out_db, SNOWFLAKE, source, profile=Profile.STANDARD)
    row = run_row(out_db, coll, "tables")
    assert row["status"] == "partial"
    assert row["error_category"] == "error"
