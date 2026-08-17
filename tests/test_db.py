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
    assert {"collections", "extract_runs", "checkpoints"} <= tables
