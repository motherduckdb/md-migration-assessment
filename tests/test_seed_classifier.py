"""The seed's edition-optionality classifier must never downgrade a real
regression to an optional skip (review 2026-08-18: candidate regexes were
prefix matches — 'ROW ACCESS POLICY ON EXTERNAL TABLE' and operational
multi-cluster errors slipped through)."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SEED = pathlib.Path(__file__).parent / "integration" / "seed.py"
spec = importlib.util.spec_from_file_location("seed_module", _SEED)
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)


@pytest.mark.parametrize(
    ("stmt", "error"),
    [
        ("CREATE MATERIALIZED VIEW v AS ...", "Unsupported feature 'MATERIALIZED VIEW'."),
        ("CREATE MATERIALIZED VIEW v AS ...", "requires Enterprise Edition"),
        ("CREATE MASKING POLICY m AS ...", "Unsupported feature 'MASKING POLICY'."),
        ("CREATE MASKING POLICY m AS ...", "Unsupported feature 'COLUMN SECURITY'."),
        ("CREATE ROW ACCESS POLICY p AS ...", "Unsupported feature 'ROW ACCESS POLICY'."),
        ("CREATE OR REPLACE TAG t", "Unsupported feature 'TAG'."),
        ("ALTER WAREHOUSE MDA_MULTI_WH SET MAX_CLUSTER_COUNT = 2",
         "Multi-cluster warehouses are an Enterprise Edition feature."),
        ("ALTER TABLE t ADD SEARCH OPTIMIZATION",
         "Unsupported feature 'SEARCH OPTIMIZATION'."),
    ],
)
def test_genuine_edition_errors_are_optional(stmt, error):
    assert seed._optional_reason(stmt, error) is not None


@pytest.mark.parametrize(
    ("stmt", "error"),
    [
        # exact reviewer repros: suffixed tokens and operational errors
        ("CREATE ROW ACCESS POLICY p AS ...",
         "Unsupported feature 'ROW ACCESS POLICY ON EXTERNAL TABLE'."),
        ("ALTER WAREHOUSE MDA_MULTI_WH SET MAX_CLUSTER_COUNT = 2",
         "Invalid configuration for multi-cluster warehouse"),
        # wrong token entirely
        ("CREATE MASKING POLICY m AS ...", "Unsupported feature 'HYBRID TABLE'."),
        ("CREATE MATERIALIZED VIEW v AS ...",
         "Unsupported feature 'MATERIALIZED VIEW ON EXTERNAL TABLE'."),
        ("CREATE OR REPLACE TAG t", "Unsupported feature 'TAG-BASED MASKING'."),
        ("ALTER TABLE t ADD SEARCH OPTIMIZATION",
         "Unsupported feature 'SEARCH OPTIMIZATION ON COLUMN'."),
        # non-edition failures in edition-dependent statements
        ("CREATE MASKING POLICY m AS ...", "SQL compilation error: syntax error at FOO"),
        ("CREATE ROW ACCESS POLICY p AS ...", "Insufficient privileges to operate on schema"),
        ("ALTER TABLE t ADD SEARCH OPTIMIZATION", "Object 'T' does not exist."),
        # non-candidate statements are never optional
        ("CREATE TABLE t (a INT)", "Unsupported feature 'MATERIALIZED VIEW'."),
    ],
)
def test_everything_else_is_fatal(stmt, error):
    assert seed._optional_reason(stmt, error) is None
