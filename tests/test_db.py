from __future__ import annotations

import os
import stat

import pytest

from md_migration_assessment.db import open_output


def test_output_file_is_0600(tmp_path):
    path = tmp_path / "a.duckdb"
    con = open_output(str(path))
    con.close()
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_local_mode_rejects_motherduck_paths(tmp_path):
    with pytest.raises(ValueError, match="upload"):
        open_output("md:some_database")


def test_meta_schema_exists(out_db):
    tables = {
        r[0]
        for r in out_db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='meta'"
        ).fetchall()
    }
    assert {"collections", "extract_runs"} <= tables
    # meta.checkpoints was planned for per-chunk resumability and dropped
    # with decision 16 (resume is per-extractor and needs no extra table)
    assert "checkpoints" not in tables


@pytest.mark.parametrize("bad", ["md:x", "MD:x", "motherduck:x", "s3://bucket/x.duckdb"])
def test_output_rejects_every_remote_scheme_spelling(bad):
    from md_migration_assessment.db import open_output

    with pytest.raises(ValueError, match="local file path"):
        open_output(bad)


def test_windows_drive_letters_are_not_schemes():
    from md_migration_assessment.db import require_local_path

    require_local_path("C:/tmp/a.duckdb", "test")  # must not raise
    require_local_path("/tmp/a.duckdb", "test")
