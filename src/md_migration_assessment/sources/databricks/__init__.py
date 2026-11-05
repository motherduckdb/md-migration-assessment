"""Databricks (Unity Catalog) source adapter.

Everything Databricks-specific lives in this package: connection and
credentials (:mod:`.connection`), the extractor manifest and its SQL
resources (:mod:`.manifest`, ``queries/``), the feature-signal taxonomy
(:mod:`.signals`), and the adapter-owned report fact builders
(:mod:`.facts`). ``ADAPTER`` is what the registry hands to the neutral core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..base import Connection, ScopeGrammar
from .facts import FACT_BUILDERS
from .manifest import EXTRACTORS, RAW_SCHEMA_VERSION, load_sql_path
from .signals import PLANNED_SIGNALS, SIGNALS

# Databricks error classes that mean "not visible to this principal / not
# enabled here" rather than a real failure.
_UNAVAILABLE_RE = re.compile(
    r"PERMISSION_DENIED"
    r"|INSUFFICIENT_PERMISSIONS"
    r"|does not have \w+ (?:\w+ )?privilege"
    r"|TABLE_OR_VIEW_NOT_FOUND"
    r"|SCHEMA_NOT_FOUND"
    r"|CATALOG_NOT_FOUND"
    r"|FEATURE_NOT_ENABLED"
    r"|UC_NOT_ENABLED"
    r"|UC_HIVE_METASTORE_DISABLED",
    re.IGNORECASE,
)

#: Unity Catalog lower-cases identifiers and quotes with backticks.
GRAMMAR = ScopeGrammar(
    identifier_re=re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$"),
    normalize=str.lower,
    quote=lambda n: "`" + n.replace("`", "``") + "`",
    levels=("catalog", "schema"),
)


@dataclass(frozen=True)
class DatabricksAdapter:
    name: str = "databricks"
    raw_schema_version: int = RAW_SCHEMA_VERSION
    extractors: list = field(default_factory=lambda: EXTRACTORS)
    scope: ScopeGrammar = GRAMMAR
    signals: list = field(default_factory=lambda: SIGNALS)
    planned_signals: list = field(default_factory=lambda: PLANNED_SIGNALS)
    fact_builders: tuple = FACT_BUILDERS

    def open(self) -> Connection:
        from .connection import DatabricksConfig, DatabricksSource

        return DatabricksSource.open(DatabricksConfig.from_env())

    def load_sql(self, path: str) -> str:
        return load_sql_path(path)

    def classify_error(self, exc: BaseException) -> str:
        return "unavailable" if _UNAVAILABLE_RE.search(str(exc)) else "failed"


ADAPTER = DatabricksAdapter()

__all__ = ["ADAPTER", "DatabricksAdapter"]
