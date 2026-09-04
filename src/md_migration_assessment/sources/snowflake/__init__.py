"""Snowflake source adapter.

Everything Snowflake-specific lives in this package: connection and
credentials (:mod:`.connection`), the extractor manifest and its SQL
resources (:mod:`.manifest`, ``queries/``), the feature-signal taxonomy
(:mod:`.signals`), and the adapter-owned report fact builders
(:mod:`.facts`). ``ADAPTER`` is what the registry hands to the neutral core.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..base import Connection, ScopeGrammar, DEFAULT_SCOPE_GRAMMAR
from .facts import FACT_BUILDERS
from .manifest import EXTRACTORS, RAW_SCHEMA_VERSION, load_sql_path
from .signals import PLANNED_SIGNALS, SIGNALS

# Snowflake error text that means "not visible to this role/edition" rather
# than a real failure. Matched on the message so tests need no connector import.
_UNAVAILABLE_RE = re.compile(
    r"does not exist or not authorized"
    r"|insufficient privileges"
    r"|not authorized"
    r"|unsupported feature",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SnowflakeAdapter:
    name: str = "snowflake"
    raw_schema_version: int = RAW_SCHEMA_VERSION
    extractors: list = field(default_factory=lambda: EXTRACTORS)
    # Snowflake upper-cases unquoted identifiers and quotes with "..."
    scope: ScopeGrammar = DEFAULT_SCOPE_GRAMMAR
    signals: list = field(default_factory=lambda: SIGNALS)
    planned_signals: list = field(default_factory=lambda: PLANNED_SIGNALS)
    fact_builders: tuple = FACT_BUILDERS

    def open(self) -> Connection:
        from .connection import SnowflakeConfig, SnowflakeSource

        return SnowflakeSource.open(SnowflakeConfig.from_env())

    def load_sql(self, path: str) -> str:
        return load_sql_path(path)

    def classify_error(self, exc: BaseException) -> str:
        return "unavailable" if _UNAVAILABLE_RE.search(str(exc)) else "failed"


ADAPTER = SnowflakeAdapter()

__all__ = ["ADAPTER", "SnowflakeAdapter"]
