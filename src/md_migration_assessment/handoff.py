"""Sanitized handoff database builder (spec §4).

Builds a separate database safe to share: meta coverage records, report.*
facts, and raw evidence filtered by a **fail-closed column policy**:

- columns classified SOURCE_BODY or QUERY_TEXT are removed;
- columns not produced by the version-controlled extract SQL are removed
  (schema drift or overridden extracts must never leak through a handoff);
- raw tables with no manifest entry at all are skipped entirely.

Included sensitive classes (object names, user identities, comments) and any
kept-but-unclassified columns are disclosed in the returned manifest.

This is the enforcement point for the privacy classifications in the
extractor manifest — the classes are behavior here, not documentation.
M4 adds upload, stronger opt-in flags, and view re-verification on top.
"""

from __future__ import annotations

import os
import re

import duckdb

from .collect.manifest import EXTRACTORS, Extractor, load_sql
from .privacy import HANDOFF_EXCLUDED_CLASSES

_ALIAS_RE = re.compile(r"\bAS\s+([A-Za-z_][A-Za-z0-9_$]*)\s*$", re.IGNORECASE)
_PROJECTION_RE = re.compile(r"\bSELECT\b(.*?)\bFROM\b", re.IGNORECASE | re.DOTALL)


def _projection_columns(sql: str) -> set[str]:
    """Output columns of an extract's SELECT projection.

    Tokenizing the whole file is not fail-closed: a word in a SQL *comment*
    (e.g. views.sql mentioning 'source_body') would whitelist a drifted
    column of that name. Only the projection list is authoritative. The
    extract files are version-controlled and rigidly shaped (line comments,
    one SELECT, one column per item, FROM) — anything unparseable is an
    error, never a pass-through.
    """
    text = " ".join(line.split("--")[0] for line in sql.splitlines())
    m = _PROJECTION_RE.search(text)
    if not m:
        raise ValueError("extract SQL has no parseable SELECT ... FROM projection")
    cols: set[str] = set()
    for item in m.group(1).split(","):
        item = item.strip()
        if not item:
            continue
        alias = _ALIAS_RE.search(item)
        if alias:
            cols.add(alias.group(1).lower())
        else:
            cols.add(item.split(".")[-1].strip('"').lower())
    return cols


def _expected_columns(ex: Extractor) -> set[str]:
    """Output columns the extractor's version-controlled SQL can produce.

    Any actual raw column outside this set cannot have been produced by the
    shipped extract — treat it as drift and drop it from the handoff.
    """
    cols: set[str] = set()
    for kind, fname in (
        ("account_usage", ex.account_usage_sql),
        ("information_schema", ex.info_schema_sql),
    ):
        if fname:
            cols |= _projection_columns(load_sql(kind, fname))
    return cols


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
    for label, p in (("source", source_path), ("destination", dest_path)):
        if p.startswith("md:") or "://" in p:
            raise ValueError(
                f"handoff {label} must be a local file path — sharing to "
                "MotherDuck is a separate, explicit upload operation"
            )
    if os.path.exists(dest_path):
        raise ValueError(f"refusing to overwrite existing file {dest_path}")
    os.umask(0o077)
    # Connect to the (writable) destination and attach the private source
    # read-only: the source is never modified by building a handoff.
    con = duckdb.connect(dest_path)
    manifest: dict = {"tables": {}, "skipped": []}
    try:
        # Ask DuckDB for the destination catalog name rather than deriving it
        # from the filename (multi-dot names like handoff.v1.duckdb differ).
        dest_db = con.execute("SELECT current_database()").fetchone()[0]
        src = source_path.replace("'", "''")
        con.execute(f"ATTACH '{src}' AS handoff_src (READ_ONLY)")
        con.execute("USE handoff_src")  # information_schema lookups read the source
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
            actual_lower = {c.lower() for c in actual}
            expected = _expected_columns(ex)
            excluded = sorted(
                col
                for col, cls in ex.sensitive_fields.items()
                if cls in HANDOFF_EXCLUDED_CLASSES and col in actual_lower
            )
            dropped_unexpected = sorted(
                c.lower()
                for c in actual
                if c.lower() not in expected and c.lower() != "collection_id"
            )
            drop = set(excluded) | set(dropped_unexpected)
            keep = [c for c in actual if c.lower() not in drop]
            col_list = ", ".join(f'"{c}"' for c in keep)
            con.execute(
                f'CREATE TABLE "{dest_db}".raw."{table}" AS '
                f'SELECT {col_list} FROM raw."{table}"'
            )
            rows = con.execute(f'SELECT count(*) FROM raw."{table}"').fetchone()[0]
            disclosed: dict[str, list[str]] = {}
            for col, cls in ex.sensitive_fields.items():
                if cls not in HANDOFF_EXCLUDED_CLASSES and col in actual_lower:
                    disclosed.setdefault(cls.value, []).append(col)
            unclassified = sorted(
                c.lower()
                for c in keep
                if c.lower() not in ex.sensitive_fields and c.lower() != "collection_id"
            )
            manifest["tables"][f"raw.{table}"] = {
                "rows": rows,
                "excluded_columns": excluded,
                "dropped_unexpected": dropped_unexpected,
                "sensitive_included": disclosed,
                "unclassified_included": unclassified,
            }
    finally:
        con.close()
    os.chmod(dest_path, 0o600)
    return manifest
