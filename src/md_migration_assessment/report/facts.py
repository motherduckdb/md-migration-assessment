"""Helpers shared by adapter fact builders.

A fact builder is ``Callable[[duckdb.DuckDBPyConnection], None]``. It must
(re)create its ``report.*`` relation with a stable shape on every call,
leaving it empty when the raw evidence it reads is absent — "relation does
not exist" is never an acceptable coverage signal; meta.extract_runs says
why the relation is empty.
"""

from __future__ import annotations

from typing import Callable

import duckdb

FactBuilder = Callable[[duckdb.DuckDBPyConnection], None]


def table_exists(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> bool:
    return bool(
        con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            [schema, name],
        ).fetchone()[0]
    )
