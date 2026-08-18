"""Sanitized handoff database builder (spec §4).

Builds a separate database safe to share: meta coverage records, report.*
facts, and raw evidence filtered by a **fail-closed column policy**:

- a column travels only if the version-controlled extract SQL's SELECT
  projection produces it (framework columns like collection_id excepted);
- among those, columns classified SOURCE_BODY or QUERY_TEXT are removed;
- anything else — drifted, injected, or from an overridden extract — is
  dropped and reported as ``dropped_unexpected``;
- raw tables with no manifest entry at all are skipped entirely.

Included sensitive classes (object names, user identities, comments) and any
kept-but-unclassified columns are disclosed in the returned manifest; the
manifest's drop categories are disjoint.

This is the enforcement point for the privacy classifications in the
extractor manifest — the classes are behavior here, not documentation.
M4 adds upload, stronger opt-in flags, and view re-verification on top.
"""

from __future__ import annotations

import os
import re

import duckdb

from . import RAW_SCHEMA_VERSION
from .collect.manifest import EXTRACTORS, Extractor, load_sql
from .collect.runner import FRAMEWORK_COLUMNS
from .db import require_local_path
from .privacy import HANDOFF_EXCLUDED_CLASSES

_BARE_COLUMN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*$")
_ALIASED_RE = re.compile(r".+\s+AS\s+([A-Za-z_][A-Za-z0-9_$]*)$", re.IGNORECASE | re.DOTALL)
_SELECT_RE = re.compile(r"\bSELECT\b", re.IGNORECASE)
_PAREN_OR_FROM_RE = re.compile(r"[()]|\bFROM\b", re.IGNORECASE)


def _projection_columns(sql: str) -> set[str]:
    """Output columns of an extract's SELECT projection — strictly parsed.

    The allowlist must be exact: a word in a comment must not whitelist a
    drifted column, a comma inside IFF(a, b, c) must not whitelist an
    argument, and EXTRACT(x FROM y) must not truncate the projection. The
    extract files are version-controlled and rigidly shaped — one SELECT,
    each item either a bare column or an expression with an explicit
    ``AS alias``. Anything else raises; a parse failure is never a
    pass-through.
    """
    text = " ".join(line.split("--")[0] for line in sql.splitlines())
    sel = _SELECT_RE.search(text)
    if not sel:
        raise ValueError("extract SQL has no SELECT")
    rest = text[sel.end():]

    # find the top-level FROM, tracking parenthesis depth so function-level
    # FROMs (EXTRACT(year FROM ts)) don't end the projection early
    depth = 0
    end = None
    for m in _PAREN_OR_FROM_RE.finditer(rest):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0:
            end = m.start()
            break
    if end is None:
        raise ValueError("extract SQL has no top-level FROM")
    projection = rest[:end]

    # split on top-level commas only
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in projection:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    items.append("".join(buf))

    cols: set[str] = set()
    for raw_item in items:
        item = raw_item.strip()
        if not item:
            raise ValueError("empty projection item (dangling comma?)")
        if _BARE_COLUMN_RE.fullmatch(item):
            cols.add(item.lower())
            continue
        aliased = _ALIASED_RE.fullmatch(item)
        if aliased:
            cols.add(aliased.group(1).lower())
            continue
        raise ValueError(
            f"unparseable projection item {item!r}: extract SQL must use "
            "bare column names or explicit 'expr AS alias'"
        )
    if not cols:
        raise ValueError("extract SQL projection produced no columns")
    return cols


def _expected_columns(ex: Extractor) -> set[str]:
    """Output columns the extractor's version-controlled definition can produce.

    SELECT extracts derive theirs from the SQL projection; SHOW extracts have
    server-defined output, so their manifest declares an explicit allowlist —
    any server-added column is drift until deliberately admitted.
    """
    cols: set[str] = set()
    for kind, fname in (
        ("account_usage", ex.account_usage_sql),
        ("information_schema", ex.info_schema_sql),
    ):
        if fname:
            cols |= _projection_columns(load_sql(kind, fname))
    if ex.show_sql:
        cols |= {c.lower() for c in ex.expected_show_columns}
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
    require_local_path(source_path, "handoff source")
    require_local_path(dest_path, "handoff destination")
    if not os.path.isfile(source_path):
        raise ValueError(f"handoff source is not an existing local file: {source_path}")
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

        # The expected-column allowlist comes from the *installed* extract
        # SQL; on version skew it would silently misclassify legitimately
        # collected columns as drift. Refuse loudly, like build_report.
        for cid, raw_version in con.execute(
            "SELECT collection_id, raw_schema_version FROM meta.collections"
        ).fetchall():
            if raw_version != RAW_SCHEMA_VERSION:
                raise ValueError(
                    f"collection {cid} has raw schema v{raw_version}; this tool "
                    f"builds handoffs for v{RAW_SCHEMA_VERSION}. Re-collect with "
                    "the current version."
                )

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

            # Disjoint partitions of the actual columns:
            #   dropped_unexpected — not produced by the shipped extract SQL
            #   excluded           — produced, but classified source-body/query-text
            #   keep               — everything else, plus framework columns
            dropped_unexpected = sorted(
                actual_lower - expected - FRAMEWORK_COLUMNS
            )
            excluded = sorted(
                col
                for col, cls in ex.sensitive_fields.items()
                if cls in HANDOFF_EXCLUDED_CLASSES
                and col in actual_lower
                and col in expected
            )
            drop = set(dropped_unexpected) | set(excluded)
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
                if c.lower() not in ex.sensitive_fields
                and c.lower() not in FRAMEWORK_COLUMNS
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
