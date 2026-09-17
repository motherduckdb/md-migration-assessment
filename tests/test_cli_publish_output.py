"""The publish command's report: human-readable by default with the Dive URL
present verbatim (terminals link it), JSON on --json."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from md_migration_assessment import cli
from md_migration_assessment.publish import PublishResult

runner = CliRunner()


def _fake_publish(monkeypatch, tmp_path, created=True):
    result = PublishResult(
        database="md_assessment_acme",
        handoff_path=tmp_path / "md_assessment_acme.handoff.duckdb",
        handoff_manifest={
            "tables": {
                "report.sizing": {"rows": 10, "excluded_columns": []},
                "raw.views": {"rows": 3, "excluded_columns": ["view_definition"]},
            },
            "skipped": [],
        },
        dive_id="7b03256b-7270-4b22-9c61-d6fd829e56ec",
        dive_url="https://app.motherduck.com/dives/7b03256b-7270-4b22-9c61-d6fd829e56ec",
        dive_created=created,
        title="Snowflake → MotherDuck migration assessment · ACME",
    )
    calls = []

    def fake(db_path, **kw):
        calls.append((Path(db_path), kw))
        return result

    import md_migration_assessment.publish as pub

    monkeypatch.setattr(pub, "publish", fake)
    return result, calls


def test_publish_prints_a_readable_report_with_the_url(monkeypatch, tmp_path):
    result, calls = _fake_publish(monkeypatch, tmp_path)
    out = runner.invoke(cli.app, ["publish", "--db", "x.duckdb", "--keep-handoff", str(tmp_path)], env={"COLUMNS": "200"})
    assert out.exit_code == 0, out.output
    assert "Published to MotherDuck" in out.output
    assert result.dive_url in out.output
    assert result.title in out.output and "(created)" in out.output
    assert "md:md_assessment_acme" in out.output
    assert "2 tables, 13 rows" in out.output
    assert "view_definition" in out.output
    assert str(result.handoff_path) in out.output
    assert calls[0][1]["keep_handoff"] is True and calls[0][1]["handoff_dir"] == tmp_path


def test_publish_json_output(monkeypatch, tmp_path):
    result, _ = _fake_publish(monkeypatch, tmp_path, created=False)
    out = runner.invoke(cli.app, ["publish", "--db", "x.duckdb", "--json"])
    assert out.exit_code == 0, out.output
    payload = json.loads(out.output)
    assert payload["dive_url"] == result.dive_url
    assert payload["dive_created"] is False
    assert payload["excluded_columns"] == ["view_definition"]
    assert payload["handoff_tables"] == 2 and payload["handoff_rows"] == 13
