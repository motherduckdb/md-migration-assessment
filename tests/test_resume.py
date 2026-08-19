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
        run_collection(out_db, source, profile=Profile.FULL)
    coll_id = out_db.execute("SELECT collection_id FROM meta.collections").fetchone()[0]

    class C:  # minimal handle for statuses()
        collection_id = coll_id

    return C


# ── progress ────────────────────────────────────────────────────────────


def test_progress_reports_every_extractor(out_db):
    lines: list[str] = []
    run_collection(
        out_db, make_source(), profile=Profile.FULL, progress=lines.append
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
    assert "mid-extract" in detail[0] and detail[1] is True
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
    # stored FULL profile won over the passed LITE: workload extracts ran
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
        out_db, make_source(), profile=Profile.FULL, resume=True,
        progress=lines.append,
    )
    assert any("resuming collection" in l for l in lines)
    assert any("databases: skipped (complete in existing collection)" in l
               for l in lines)


def test_resume_refuses_a_different_account(out_db):
    interrupted_collection(out_db)
    other = make_source()
    other.account = "OTHERACCT"
    with pytest.raises(ValueError, match="OTHERACCT"):
        run_collection(out_db, other, profile=Profile.FULL, resume=True)


def test_resume_requires_an_existing_collection(out_db):
    with pytest.raises(ValueError, match="nothing to resume"):
        run_collection(out_db, make_source(), profile=Profile.FULL, resume=True)


def test_fresh_collect_on_used_database_mentions_resume(out_db):
    run_collection(out_db, make_source(), profile=Profile.LITE)
    with pytest.raises(ValueError, match="--resume"):
        run_collection(out_db, make_source(), profile=Profile.LITE)
