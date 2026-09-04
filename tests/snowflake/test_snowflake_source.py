"""Regression tests for the connector→Arrow→DuckDB seam.

snowflake-connector-python's fetch_arrow_batches() yields pyarrow.Table
objects, not RecordBatches — feeding those into RecordBatchReader.from_batches
constructs fine and then explodes when DuckDB consumes the stream (found live
against a trial account, 2026-08-17)."""

from __future__ import annotations

import duckdb
import pyarrow as pa

from md_migration_assessment.sources.snowflake.connection import as_record_batches


def _tables():
    return [
        pa.table({"a": [1, 2], "b": ["x", "y"]}),
        pa.table({"a": [3], "b": ["z"]}),
    ]


def test_as_record_batches_flattens_tables():
    batches = list(as_record_batches(_tables()))
    assert all(isinstance(b, pa.RecordBatch) for b in batches)
    assert sum(b.num_rows for b in batches) == 3


def test_as_record_batches_passes_batches_through():
    batch = pa.record_batch({"a": [1]})
    assert list(as_record_batches([batch])) == [batch]


def test_duckdb_can_consume_reader_built_from_connector_style_tables():
    """End-to-end for the exact failure mode: chunked Tables → reader → DuckDB."""
    tables = _tables()
    reader = pa.RecordBatchReader.from_batches(
        tables[0].schema, as_record_batches(iter(tables))
    )
    con = duckdb.connect()
    con.register("stream", reader)
    n = con.execute("SELECT count(*), sum(a) FROM stream").fetchone()
    assert n == (3, 6)
    con.close()


def test_ensure_typed_replaces_null_columns_from_empty_show_results():
    """Regression (found live): an empty SHOW result infers null-typed
    columns, which DuckDB binds as non-text and string probes fail."""
    from md_migration_assessment.sources.snowflake.connection import ensure_typed

    empty = pa.table({"name": pa.array([], pa.null()), "n": pa.array([], pa.int64())})
    fixed = ensure_typed(empty)
    assert fixed.schema.field("name").type == pa.string()
    assert fixed.schema.field("n").type == pa.int64()  # real types untouched

    con = duckdb.connect()
    con.register("t", fixed)
    # the exact predicate shape the live probes use must bind
    n = con.execute("SELECT count(*) FROM t WHERE name NOT LIKE 'USER$%'").fetchone()[0]
    assert n == 0
    con.close()
