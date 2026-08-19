"""Progress reporting, graceful interruption, and --resume.

The contract: a Ctrl+C at any point leaves a valid partial collection —
every extractor has an honest coverage row ('interrupted' = not collected,
never zero), finished_at stays NULL, and resume re-runs exactly what is not
complete/not_requested using the STORED collection parameters.
"""

from __future__ import annotations

import pytest

from conftest import FakeSource
from fixtures import REALISTIC, REALISTIC_SHOW, WORKLOAD

from md_migration_assessment.collect.manifest import EXTRACTORS, Profile
from md_migration_assessment.collect.runner import run_collection


def full_au(**overrides) -> dict:
    au = dict(REALISTIC)
    au.update(WORKLOAD)
    au.update(overrides)
    return au


def make_source(**overrides) -> FakeSource:
    return FakeSource(
        account_usage=full_au(**overrides),
        databases=["APPDB"],
        show_data=dict(REALISTIC_SHOW),
    )


def statuses(con, coll):
    return dict(
        con.execute(
            "SELECT extractor, status FROM meta.extract_runs WHERE collection_id = ?",
            [str(coll.collection_id)],
        ).fetchall()
    )


def interrupted_collection(out_db):
    """Run a full-profile collection that gets Ctrl+C'd at raw 'tables'."""
    source = make_source(tables=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run_collection(out_db, source, profile=Profile.STANDARD)
    coll_id = out_db.execute("SELECT collection_id FROM meta.collections").fetchone()[0]

    class C:  # minimal handle for statuses()
        collection_id = coll_id

    return C


# ── progress ────────────────────────────────────────────────────────────


def test_progress_reports_every_extractor(out_db):
    lines: list[str] = []
    run_collection(
        out_db, make_source(), profile=Profile.STANDARD, progress=lines.append
    )
    running = [l for l in lines if l.endswith(": running")]
    done = [l for l in lines if ": complete (" in l]
    assert len(running) == len(EXTRACTORS)
    assert len(done) == len(EXTRACTORS)
    assert lines[0].startswith("[1/")
    assert "rows" in done[0] and "s)" in done[0]


# ── graceful interruption ───────────────────────────────────────────────


def test_interrupt_leaves_valid_partial_collection(out_db):
    coll = interrupted_collection(out_db)
    st = statuses(out_db, coll)
    # every extractor has exactly one row
    assert set(st) == {e.name for e in EXTRACTORS}
    # extractors before the interrupt completed and their evidence persists
    assert st["databases"] == "complete"
    assert out_db.execute("SELECT count(*) FROM raw.databases").fetchone()[0] > 0
    # the in-flight extractor and everything after are visibly interrupted
    assert st["tables"] == "interrupted"
    assert st["query_concurrency"] == "interrupted"
    detail = out_db.execute(
        "SELECT error_detail, retryable FROM meta.extract_runs "
        "WHERE extractor = 'tables'"
    ).fetchone()
    assert "interrupted" in detail[0] and "--resume" in detail[0]
    assert detail[1] is True
    # the collection is visibly unfinished
    finished = out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0]
    assert finished is None
    # interrupted rows surface in the gaps view
    n_gaps = out_db.execute(
        "SELECT count(*) FROM meta.gaps WHERE status = 'interrupted'"
    ).fetchone()[0]
    assert n_gaps > 0


def test_interrupted_collection_still_builds_a_report(out_db):
    from md_migration_assessment.report import build_report

    coll = interrupted_collection(out_db)
    build_report(out_db)
    rows = out_db.execute(
        """
        SELECT feature, observation_status, note FROM report.feature_inventory
        WHERE source_extractor = 'tables' AND collection_id = ?
        """,
        [str(coll.collection_id)],
    ).fetchall()
    assert rows
    for _, obs, note in rows:
        assert obs == "unknown"  # interrupted is missing evidence, never zero
        assert "interrupted" in note


# ── resume ──────────────────────────────────────────────────────────────


def test_resume_completes_only_what_is_missing(out_db):
    interrupted_collection(out_db)
    source = make_source()  # no bomb this time
    coll = run_collection(
        out_db, source, profile=Profile.LITE, resume=True  # LITE must be ignored
    )
    st = statuses(out_db, coll)
    # the stored STANDARD profile won over the passed LITE: workload
    # extracts (standard-only) ran
    assert st["query_concurrency"] == "complete"
    assert set(st.values()) == {"complete"}
    assert len(st) == len(EXTRACTORS)  # still exactly one row per extractor
    # already-complete extractors were not re-queried
    assert not any("account_usage.databases" in q for q in source.queries)
    # ...but the interrupted one was, and its evidence landed
    assert any("account_usage.tables" in q for q in source.queries)
    assert out_db.execute("SELECT count(*) FROM raw.tables").fetchone()[0] > 0
    finished = out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0]
    assert finished is not None


def test_resume_reports_skips_in_progress(out_db):
    interrupted_collection(out_db)
    lines: list[str] = []
    run_collection(
        out_db, make_source(), profile=Profile.STANDARD, resume=True,
        progress=lines.append,
    )
    assert any("resuming collection" in l for l in lines)
    assert any("databases: skipped (complete in existing collection)" in l
               for l in lines)


def test_interrupt_outside_extractor_still_records_all_rows(out_db):
    """Review, 2026-08-19: the guard must cover the whole orchestration —
    a Ctrl+C landing in a progress write (not inside an extractor) must
    still leave every extractor with a coverage row."""
    calls = {"n": 0}

    def exploding_progress(msg: str) -> None:
        calls["n"] += 1
        if calls["n"] == 5:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_collection(
            out_db, make_source(), profile=Profile.STANDARD,
            progress=exploding_progress,
        )
    rows = dict(out_db.execute(
        "SELECT extractor, status FROM meta.extract_runs"
    ).fetchall())
    assert set(rows) == {e.name for e in EXTRACTORS}
    assert "interrupted" in set(rows.values())
    finished = out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0]
    assert finished is None


def test_failed_retry_leaves_no_stale_raw_evidence(out_db):
    """Review, 2026-08-19: a resume retry that fails before ingesting must
    not leave the previous attempt's raw rows behind a 'failed' status —
    the report would materialize stale evidence."""
    run_collection(out_db, make_source(), profile=Profile.STANDARD)
    assert out_db.execute("SELECT count(*) FROM raw.tables").fetchone()[0] > 0
    # simulate a prior run whose 'tables' extract needs a retry
    out_db.execute(
        "UPDATE meta.extract_runs SET status = 'failed' WHERE extractor = 'tables'"
    )
    # the retry fails outright (generic error -> no INFORMATION_SCHEMA save)
    source = make_source(tables=RuntimeError("boom"))
    source.info_schema = {"tables": {"APPDB": RuntimeError("boom")}}
    run_collection(out_db, source, profile=Profile.STANDARD, resume=True)
    status = out_db.execute(
        "SELECT status FROM meta.extract_runs WHERE extractor = 'tables'"
    ).fetchone()[0]
    assert status == "failed"
    # the stale raw table is gone, not silently attributed to this collection
    exists = out_db.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'raw' AND table_name = 'tables'"
    ).fetchone()[0]
    assert exists == 0
    # and the report materializes nothing from it
    from md_migration_assessment.report import build_report

    build_report(out_db)
    assert out_db.execute("SELECT count(*) FROM report.sizing").fetchone()[0] == 0


def test_stale_raw_without_coverage_row_is_still_dropped(out_db):
    """Review round 2, 2026-08-19: a kill between raw ingestion and coverage
    recording leaves raw data with NO coverage row. Resume treats the
    extractor as missing — the raw target must be dropped anyway, so a
    retry that fails cannot resurrect the stale evidence."""
    run_collection(out_db, make_source(), profile=Profile.STANDARD)
    assert out_db.execute("SELECT count(*) FROM raw.tables").fetchone()[0] > 0
    # simulate the kill window: raw rows exist, coverage row does not
    out_db.execute("DELETE FROM meta.extract_runs WHERE extractor = 'tables'")
    out_db.execute("UPDATE meta.collections SET finished_at = NULL")
    source = make_source(tables=RuntimeError("boom"))
    source.info_schema = {"tables": {"APPDB": RuntimeError("boom")}}
    run_collection(out_db, source, profile=Profile.STANDARD, resume=True)
    assert out_db.execute(
        "SELECT status FROM meta.extract_runs WHERE extractor = 'tables'"
    ).fetchone()[0] == "failed"
    assert out_db.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'raw' AND table_name = 'tables'"
    ).fetchone()[0] == 0
    from md_migration_assessment.report import build_report

    build_report(out_db)
    assert out_db.execute("SELECT count(*) FROM report.sizing").fetchone()[0] == 0


def test_interrupt_after_finish_commit_clears_finished_at(out_db, monkeypatch):
    """Review round 2, 2026-08-19: Ctrl+C delivered after finish_collection
    commits but before it returns must not leave the collection stamped
    finished — a run that exits by interrupt never claims completion."""
    from md_migration_assessment import db as db_module

    real_finish = db_module.finish_collection

    def finish_then_interrupt(con, coll):
        real_finish(con, coll)
        raise KeyboardInterrupt

    monkeypatch.setattr(db_module, "finish_collection", finish_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_collection(out_db, make_source(), profile=Profile.STANDARD)
    assert out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0] is None
    # all coverage rows are present, so a resume just re-stamps it
    st = statuses_all(out_db)
    assert set(st) == {e.name for e in EXTRACTORS}
    monkeypatch.setattr(db_module, "finish_collection", real_finish)
    run_collection(out_db, make_source(), profile=Profile.STANDARD, resume=True)
    assert out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0]


def statuses_all(con):
    return dict(con.execute(
        "SELECT extractor, status FROM meta.extract_runs"
    ).fetchall())


def test_resume_reopens_a_finished_collection(out_db):
    """Review, 2026-08-19: a finished collection with failed extractors is
    resumable; an interrupted retry must not leave finished_at set while
    extractors say interrupted."""
    run_collection(out_db, make_source(), profile=Profile.STANDARD)
    assert out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0]
    out_db.execute(
        "UPDATE meta.extract_runs SET status = 'unavailable' WHERE extractor = 'tables'"
    )
    with pytest.raises(KeyboardInterrupt):
        run_collection(
            out_db, make_source(tables=KeyboardInterrupt()),
            profile=Profile.STANDARD, resume=True,
        )
    finished = out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0]
    assert finished is None
    status = out_db.execute(
        "SELECT status FROM meta.extract_runs WHERE extractor = 'tables'"
    ).fetchone()[0]
    assert status == "interrupted"
    # and a second resume completes and re-stamps finished_at
    run_collection(out_db, make_source(), profile=Profile.STANDARD, resume=True)
    assert out_db.execute("SELECT finished_at FROM meta.collections").fetchone()[0]


def test_resume_refuses_a_different_account(out_db):
    interrupted_collection(out_db)
    other = make_source()
    other.account = "OTHERACCT"
    with pytest.raises(ValueError, match="OTHERACCT"):
        run_collection(out_db, other, profile=Profile.STANDARD, resume=True)


def test_resume_requires_an_existing_collection(out_db):
    with pytest.raises(ValueError, match="nothing to resume"):
        run_collection(out_db, make_source(), profile=Profile.STANDARD, resume=True)


def test_fresh_collect_on_used_database_mentions_resume(out_db):
    run_collection(out_db, make_source(), profile=Profile.LITE)
    with pytest.raises(ValueError, match="--resume"):
        run_collection(out_db, make_source(), profile=Profile.LITE)
