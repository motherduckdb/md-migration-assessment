"""Privacy ship gate (spec §4): sensitive fields are classified per extractor,
and the classes drive handoff behavior."""

from __future__ import annotations

from md_migration_assessment.collect.manifest import EXTRACTORS
from md_migration_assessment.privacy import (
    HANDOFF_DISCLOSED_CLASSES,
    HANDOFF_EXCLUDED_CLASSES,
    PrivacyClass,
)


def test_every_class_has_a_handoff_policy():
    covered = HANDOFF_EXCLUDED_CLASSES | HANDOFF_DISCLOSED_CLASSES
    assert covered == set(PrivacyClass), (
        "every privacy class must be explicitly excluded-from or "
        "disclosed-in the default handoff database"
    )


def test_source_bodies_and_query_text_never_reach_default_handoff():
    assert PrivacyClass.SOURCE_BODY in HANDOFF_EXCLUDED_CLASSES
    assert PrivacyClass.QUERY_TEXT in HANDOFF_EXCLUDED_CLASSES


def test_all_declared_classes_are_valid():
    for ex in EXTRACTORS:
        for column, cls in ex.sensitive_fields.items():
            assert isinstance(cls, PrivacyClass), (ex.name, column)


def test_catalog_extracts_declare_owner_identity():
    """Owner columns are user identities; forgetting the classification would
    silently include them without manifest disclosure."""
    for name in ("databases", "schemata", "tables", "views", "functions", "procedures"):
        ex = next(e for e in EXTRACTORS if e.name == name)
        assert PrivacyClass.USER_IDENTITY in ex.sensitive_fields.values(), name
