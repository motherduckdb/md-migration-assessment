"""The source-adapter contract.

Everything the collector knows about a particular warehouse lives in one
adapter package under :mod:`md_migration_assessment.sources`. The neutral
core (runner, meta schema, privacy classes, handoff policy, report
observation contract) talks to the warehouse only through the two
protocols below:

- :class:`SourceAdapter` — static knowledge: how to connect, which
  extractors exist and how their SQL is loaded, how to tell a privilege
  gap from a real failure, how identifiers are quoted, which report
  signals and fact builders apply.
- :class:`Connection` — a live session: run a query as Arrow, run a
  client-materialized command, enumerate databases, read the server clock.

The surface is deliberately the set of hooks the code already needed for
Snowflake, no more. Add a hook when a second source asks for it, not
before.

Profiles (``lite`` / ``standard``) are a neutral ordering in
:mod:`md_migration_assessment.collect.extractor`; what a tier *means* for
a given warehouse is defined by which extractors and acquisition
strategies the adapter assigns to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Iterable, Protocol, runtime_checkable

import duckdb
import pyarrow as pa

if TYPE_CHECKING:
    from ..collect.extractor import Extractor
    from ..report.signals import PlannedSignal, Signal


@dataclass(frozen=True)
class SessionInfo:
    """Identity of the deployment a connection is talking to.

    ``deployment`` is whatever uniquely names the source installation
    (a Snowflake account, a BigQuery project, a Postgres host/database);
    it is stored in ``meta.collections.source_deployment`` and used to
    refuse a ``--resume`` against a different deployment.
    """

    deployment: str | None
    version: str | None
    region: str | None = None
    edition: str | None = None


@runtime_checkable
class Connection(Protocol):
    """A live read-only session with the source. Tests substitute fakes."""

    def reader(self, sql: str) -> pa.RecordBatchReader | pa.Table:
        """Execute ``sql`` and stream its result as Arrow."""
        ...

    def command(self, command: str) -> tuple[pa.Table, bool]:
        """Run a client-materialized command (e.g. Snowflake ``SHOW``).

        Returns ``(table, truncated)``; ``truncated`` is True when the
        server capped the output, so the runner records partial coverage
        instead of presenting a truncated inventory as complete.
        """
        ...

    def list_databases(self) -> list[str]:
        """Enumerate the top-level containers a per-database walk visits."""
        ...

    def session_info(self) -> SessionInfo: ...

    def server_time(self) -> datetime:
        """The server's own UTC clock (coverage windows anchor to it)."""
        ...

    def close(self) -> None: ...


#: SQL-standard identifier: what ``--scope`` entries may look like.
DEFAULT_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def double_quote(name: str) -> str:
    """Quote an identifier with double quotes (SQL standard, Snowflake,
    Postgres, Redshift, DuckDB). BigQuery would supply backticks."""
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class ScopeGrammar:
    """How ``--scope`` entries and discovered database names are handled.

    ``levels`` names the two scope granularities for error messages; the
    runner's scope logic is fixed at two levels (a container and a
    sub-container), which every supported warehouse maps onto.
    """

    identifier_re: re.Pattern = DEFAULT_IDENTIFIER_RE
    #: case-fold applied to --scope entries before matching (Snowflake
    #: and most warehouses upper-case unquoted identifiers).
    normalize: Callable[[str], str] = str.upper
    quote: Callable[[str], str] = double_quote
    levels: tuple[str, str] = ("database", "schema")


DEFAULT_SCOPE_GRAMMAR = ScopeGrammar()


@runtime_checkable
class SourceAdapter(Protocol):
    """Static description of one warehouse. One instance per source kind."""

    #: registry key and ``meta.collections.source_kind`` value
    name: str
    #: version of this adapter's raw.* table shapes (bump on any extract
    #: column change; recorded per collection, checked by report/handoff)
    raw_schema_version: int
    extractors: list["Extractor"]
    scope: ScopeGrammar
    signals: list["Signal"]
    planned_signals: list["PlannedSignal"]
    #: report fact builders, run once per output database after the
    #: feature inventory; each must leave its relation existing (possibly
    #: empty) whatever evidence is present
    fact_builders: Iterable[Callable[[duckdb.DuckDBPyConnection], None]]

    def open(self) -> Connection:
        """Open a live connection from the process environment."""
        ...

    def load_sql(self, path: str) -> str:
        """Load an extract SQL resource by adapter-relative path."""
        ...

    def classify_error(self, exc: BaseException) -> str:
        """``'unavailable'`` for a privilege/visibility/edition gap that
        should fall through to the next acquisition strategy, else
        ``'failed'`` (a real error, recorded as such, no fallback)."""
        ...
