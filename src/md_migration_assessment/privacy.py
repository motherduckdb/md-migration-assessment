"""Sensitive-field classification.

Every raw column that carries customer-sensitive content is declared in the
extractor manifest with one of these classes. The classification drives:

- local retention policy (what is written to the private database at all),
- the handoff builder (which fields the default reduced handoff database
  excludes), and
- the manifest printed when a handoff is built.

Privacy is a property of each extractor and field, not a delivery feature:
an extractor may not ship until its sensitive fields are classified and its
retention behavior is tested.
"""

from __future__ import annotations

from enum import Enum


class PrivacyClass(str, Enum):
    # Executable or definitional source code: view definitions, UDF/procedure
    # bodies. Retained locally (needed for dialect/code assessment); excluded
    # from the default handoff database.
    SOURCE_BODY = "source_body"

    # Raw SQL text of workload queries. No current collection profile retains it.
    QUERY_TEXT = "query_text"

    # Database/schema/table/column identifiers. Retained locally and included
    # in the default handoff (report drill-downs need them); disclosed in the
    # handoff manifest.
    OBJECT_NAME = "object_name"

    # User names, role names, client IPs. Retained locally; aggregated or
    # included with disclosure in the handoff manifest.
    USER_IDENTITY = "user_identity"

    # Free-text comments on objects (can contain anything).
    COMMENT = "comment"


#: Classes the default reduced handoff database must exclude entirely.
HANDOFF_EXCLUDED_CLASSES = frozenset({PrivacyClass.SOURCE_BODY, PrivacyClass.QUERY_TEXT})

#: Classes included in the default handoff but listed in its manifest.
HANDOFF_DISCLOSED_CLASSES = frozenset(
    {PrivacyClass.OBJECT_NAME, PrivacyClass.USER_IDENTITY, PrivacyClass.COMMENT}
)
