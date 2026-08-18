"""Sanitized handoff database builder (spec §4).

Builds a separate database safe to share: meta coverage records, report.*
facts, and raw evidence with every column classified SOURCE_BODY or
QUERY_TEXT removed. OBJECT_NAME / USER_IDENTITY / COMMENT columns are
included (drill-downs need them) and disclosed in the returned manifest.

This is the enforcement point for the privacy classifications in the
extractor manifest — the classes are behavior here, not documentation.
M4 adds upload, stronger opt-in flags, and view re-verification on top.
"""

from __future__ import annotations

import os

import duckdb

from .collect.manifest import EXTRACTORS
from .privacy import HANDOFF_EXCLUDED_CLASSES


def _columns(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [schema, table],
        ).fetchall()
    ]


def _tables(con: duckdb.DuckDBPyConnection, schema: str) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = ?",
            [schema],
        ).fetchall()
    ]


def build_handoff(source_path: str, dest_path: str) -> dict:
    """Build the sanitized handoff database. Returns its manifest."""
    if os.path.exists(dest_path):
        raise ValueError(f"refusing to overwrite existing file {dest_path}")
    os.umask(0o077)
    # Connect to the (writable) destination and attach the private source
    # read-only: the source is never modified by building a handoff.
    con = duckdb.connect(dest_path)
    manifest: dict = {"tables": {}, "skipped": []}
    try:
        src = source_path.replace("'", "''")
        con.execute(f"ATTACH '{src}' AS handoff_src (READ_ONLY)")
        con.execute("USE handoff_src")  # information_schema lookups read the source
        dest_db = os.path.splitext(os.path.basename(dest_path))[0]
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{dest_db}".meta')
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{dest_db}".raw')
        con.execute(f'CREATE SCHEMA IF NOT EXISTS "{dest_db}".report')

        # meta + report travel wholesale: coverage records and facts contain
        # no source bodies or query text by construction.
        for schema in ("meta", "report"):
            for table in _tables(con, schema):
                con.execute(
                    f'CREATE TABLE "{dest_db}"."{schema}"."{table}" AS '
                    f'SELECT * FROM "{schema}"."{table}"'
                )
                rows = con.execute(
                    f'SELECT count(*) FROM "{schema}"."{table}"'
                ).fetchone()[0]
                manifest["tables"][f"{schema}.{table}"] = {
                    "rows": rows, "excluded_columns": [], "sensitive_included": {},
                }

        by_target = {ex.target_table: ex for ex in EXTRACTORS}
        for table in _tables(con, "raw"):
            ex = by_target.get(table)
            if ex is None:
                # Unknown raw table (e.g. from an overridden extract): no
                # classification exists, so it must not travel.
                manifest["skipped"].append(table)
                continue
            actual = _columns(con, "raw", table)
            excluded = sorted(
                col
                for col, cls in ex.sensitive_fields.items()
                if cls in HANDOFF_EXCLUDED_CLASSES and col in {c.lower() for c in actual}
            )
            keep = [c for c in actual if c.lower() not in set(excluded)]
            col_list = ", ".join(f'"{c}"' for c in keep)
            con.execute(
                f'CREATE TABLE "{dest_db}".raw."{table}" AS SELECT {col_list} FROM raw."{table}"'
            )
            rows = con.execute(f'SELECT count(*) FROM raw."{table}"').fetchone()[0]
            disclosed: dict[str, list[str]] = {}
            for col, cls in ex.sensitive_fields.items():
                if cls not in HANDOFF_EXCLUDED_CLASSES and col in {c.lower() for c in actual}:
                    disclosed.setdefault(cls.value, []).append(col)
            manifest["tables"][f"raw.{table}"] = {
                "rows": rows,
                "excluded_columns": excluded,
                "sensitive_included": disclosed,
            }
    finally:
        con.close()
    os.chmod(dest_path, 0o600)
    return manifest
