from __future__ import annotations

import pytest


@pytest.fixture()
def out_db(tmp_path):
    from md_migration_assessment.db import open_output

    con = open_output(str(tmp_path / "assessment.duckdb"))
    yield con
    con.close()
