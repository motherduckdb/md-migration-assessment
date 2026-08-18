"""The manifest is a contract: SQL resources must load, carry the expected
placeholders, and declare their sensitive fields against real columns."""

from __future__ import annotations

import re

import pytest

from md_migration_assessment.collect.manifest import (
    EXTRACTORS,
    Profile,
    extractor_version,
    extractors_for,
    load_sql,
)


@pytest.mark.parametrize("ex", EXTRACTORS, ids=lambda e: e.name)
def test_sql_resources_load_and_have_placeholders(ex):
    if ex.account_usage_sql:
        sql = load_sql("account_usage", ex.account_usage_sql)
        assert "{scope_filter}" in sql
        assert f"account_usage.{ex.name}" in sql.lower()
        if ex.window_days is not None:
            assert "{window_days}" in sql
    if ex.info_schema_sql:
        sql = load_sql("information_schema", ex.info_schema_sql)
        assert "{database}" in sql
        assert "{scope_filter}" in sql


@pytest.mark.parametrize("ex", EXTRACTORS, ids=lambda e: e.name)
def test_sensitive_fields_reference_real_columns(ex):
    """Guard against drift between the privacy declarations and the SQL."""
    if not ex.account_usage_sql:
        return
    sql = load_sql("account_usage", ex.account_usage_sql).lower()
    for column in ex.sensitive_fields:
        assert re.search(rf"\b{re.escape(column)}\b", sql), (
            f"{ex.name}: sensitive field {column!r} not present in extract SQL"
        )


@pytest.mark.parametrize("ex", EXTRACTORS, ids=lambda e: e.name)
def test_scope_columns_reference_real_columns(ex):
    if not ex.account_usage_sql:
        return
    sql = load_sql("account_usage", ex.account_usage_sql).lower()
    for column in ex.scope_columns.values():
        assert column.lower() in sql


def test_extractor_version_is_stable_content_hash():
    ex = EXTRACTORS[0]
    v1, v2 = extractor_version(ex), extractor_version(ex)
    assert v1 == v2
    assert re.fullmatch(r"[0-9a-f]{12}", v1)


def test_profiles_nest():
    lite = {e.name for e in extractors_for(Profile.LITE)}
    standard = {e.name for e in extractors_for(Profile.STANDARD)}
    full = {e.name for e in extractors_for(Profile.FULL)}
    assert lite < standard <= full
    assert "table_storage_metrics" in standard - lite


def test_source_bodies_are_declared():
    """Every extract that lands executable/definitional source must classify it."""
    from md_migration_assessment.privacy import PrivacyClass

    by_name = {e.name: e for e in EXTRACTORS}
    assert by_name["views"].sensitive_fields["view_definition"] is PrivacyClass.SOURCE_BODY
    assert (
        by_name["functions"].sensitive_fields["function_definition"]
        is PrivacyClass.SOURCE_BODY
    )
    assert (
        by_name["procedures"].sensitive_fields["procedure_definition"]
        is PrivacyClass.SOURCE_BODY
    )


def test_show_extractors_declare_their_handoff_allowlist():
    """SHOW output is server-defined: without an explicit expected-column
    allowlist the handoff would drop everything (or, worse, trust drift)."""
    for ex in EXTRACTORS:
        if ex.show_sql is None:
            continue
        assert ex.expected_show_columns, ex.name
        allow = {c.lower() for c in ex.expected_show_columns}
        for col in ex.sensitive_fields:
            assert col in allow, (ex.name, col)
        # SHOW extracts have exactly one source
        assert ex.account_usage_sql is None and ex.info_schema_sql is None, ex.name
