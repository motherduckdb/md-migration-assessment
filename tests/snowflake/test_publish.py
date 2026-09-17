"""`md-assess publish`: uploads the HANDOFF (never the private collection) and
creates or updates the Dive over it, all through plain SQL on a MotherDuck
connection. The connection is faked here; the handoff build is real."""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

from fake_snowflake import FakeSource
from fixtures import REALISTIC, REALISTIC_SHOW

from md_migration_assessment import publish as pub
from md_migration_assessment.collect.runner import run_collection
from md_migration_assessment.db import open_output
from md_migration_assessment.report import build_report
from md_migration_assessment.sources.snowflake import ADAPTER as SNOWFLAKE
from md_migration_assessment.sources.snowflake.manifest import Profile


@pytest.fixture()
def assessed_db(tmp_path) -> Path:
    path = tmp_path / "private.duckdb"
    con = open_output(str(path))
    run_collection(
        con, SNOWFLAKE,
        FakeSource(account_usage=dict(REALISTIC), databases=["APPDB"], show_data=dict(REALISTIC_SHOW)),
        profile=Profile.STANDARD,
    )
    build_report(con)
    con.close()
    return path


@pytest.fixture()
def dive_tsx(tmp_path) -> Path:
    p = tmp_path / "dive.tsx"
    p.write_text(
        "export const REQUIRED_DATABASES = [{ type: 'database', path: 'md:md_assessment', alias: 'assessment' }];\n"
        "export default function D() { return null; }\n"
    )
    return p


class FakeMotherDuck:
    """Records every statement; answers the Dive functions with canned rows."""

    def __init__(self, *, existing_dives=(), database_exists=False):
        self.statements: list[str] = []
        self.existing_dives = list(existing_dives)
        self.database_exists = database_exists
        self.closed = False
        self._last: list[tuple] = []

    def execute(self, sql: str):
        self.statements.append(sql)
        up = sql.lstrip().upper()
        if up.startswith("CREATE DATABASE"):
            if self.database_exists:
                raise duckdb.CatalogException("Catalog Error: database already exists")
            self.database_exists = True
        elif up.startswith("DROP DATABASE"):
            self.database_exists = False
        elif "MD_LIST_DIVES" in up:
            self._last = [(d,) for d in self.existing_dives]
        elif "MD_CREATE_DIVE" in up:
            self._last = [("11111111-2222-4333-8444-555555555555",)]
        else:
            self._last = []
        return self

    def fetchall(self):
        return list(self._last)

    def fetchone(self):
        return self._last[0] if self._last else None

    def close(self):
        self.closed = True


def _publish(assessed_db, dive_tsx, tmp_path, fake, **kw):
    return pub.publish(
        assessed_db, handoff_dir=tmp_path / "handoff", connect=lambda: fake, dive_source_path=dive_tsx, **kw
    )


def test_publish_uploads_the_handoff_and_creates_the_dive(assessed_db, dive_tsx, tmp_path):
    fake = FakeMotherDuck()
    result = _publish(assessed_db, dive_tsx, tmp_path, fake)

    # the uploaded file is the handoff, built for real: no source bodies, facts intact
    assert result.handoff_path is not None and result.handoff_path.is_file()
    assert result.handoff_path != assessed_db
    con = duckdb.connect(str(result.handoff_path), read_only=True)
    cols = {r[0].lower() for r in con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'raw' AND table_name = 'views'"
    ).fetchall()}
    assert "view_definition" not in cols
    assert con.execute("SELECT count(*) FROM report.feature_inventory").fetchone()[0] > 0
    assert con.execute("SELECT count(*) FROM report.schema_version").fetchone()[0] == 1
    con.close()
    assert stat_mode(result.handoff_path) == 0o600

    create_db = [s for s in fake.statements if s.startswith("CREATE DATABASE")]
    assert create_db == [f'CREATE DATABASE "{result.database}" FROM \'{result.handoff_path}\'']
    assert result.database.startswith("md_assessment")

    create_dive = [s for s in fake.statements if "MD_CREATE_DIVE" in s]
    assert len(create_dive) == 1 and not any("MD_UPDATE_DIVE" in s for s in fake.statements)
    assert f"md:{result.database}" in create_dive[0]
    assert "md:md_assessment'" not in create_dive[0].replace(f"md:{result.database}", "")
    assert f"'alias': 'assessment'" in create_dive[0]
    assert "no source bodies or query text" in create_dive[0]
    assert result.dive_created is True
    assert result.dive_url == "https://app.motherduck.com/dives/11111111-2222-4333-8444-555555555555"
    assert fake.closed


def test_publish_updates_an_existing_dive_by_title(assessed_db, dive_tsx, tmp_path):
    fake = FakeMotherDuck(existing_dives=["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"])
    result = _publish(assessed_db, dive_tsx, tmp_path, fake, title="My assessment")
    assert result.dive_created is False
    assert result.dive_id == "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    kinds = [s.split("(")[0].strip() for s in fake.statements if "MD_" in s]
    assert kinds == ["SELECT id FROM MD_LIST_DIVES", "FROM MD_UPDATE_DIVE_CONTENT", "FROM MD_UPDATE_DIVE_METADATA"]
    assert "title = 'My assessment'" in fake.statements[-1]


def test_publish_refuses_existing_database_unless_replace(assessed_db, dive_tsx, tmp_path):
    fake = FakeMotherDuck(database_exists=True)
    with pytest.raises(ValueError, match="--replace"):
        _publish(assessed_db, dive_tsx, tmp_path, fake, database="md_assessment_x")
    assert not any("MD_CREATE_DIVE" in s for s in fake.statements)  # nothing else happened

    fake = FakeMotherDuck(database_exists=True)
    result = _publish(assessed_db, dive_tsx, tmp_path / "again", fake, database="md_assessment_x", replace=True)
    ops = [" ".join(s.split(" ")[:2]) for s in fake.statements if s.startswith(("CREATE DATABASE", "DROP DATABASE"))]
    assert ops == ["CREATE DATABASE", "DROP DATABASE", "CREATE DATABASE"]
    assert result.database == "md_assessment_x"


def test_publish_fails_fast_without_bundle_or_token(assessed_db, tmp_path, monkeypatch):
    fake = FakeMotherDuck()
    with pytest.raises(FileNotFoundError, match="npm run bundle"):
        pub.publish(assessed_db, connect=lambda: fake, dive_source_path=tmp_path / "missing.tsx")
    assert fake.statements == []  # never connected
    for k in pub.TOKEN_ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="MOTHERDUCK_TOKEN"):
        pub.resolve_token()
    monkeypatch.setenv("MOTHERDUCK_TOKEN", "t0k")
    assert pub.resolve_token() == "t0k"


def test_database_name_and_identifier_validation(assessed_db, dive_tsx, tmp_path):
    assert pub.database_name_for("MYORG-MYACCOUNT") == "md_assessment_myorg_myaccount"
    assert pub.database_name_for(None) == "md_assessment"
    assert pub.database_name_for("weird name!!") == "md_assessment_weird_name"
    with pytest.raises(ValueError, match="identifier"):
        _publish(assessed_db, dive_tsx, tmp_path, FakeMotherDuck(), database="bad-name")


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777
