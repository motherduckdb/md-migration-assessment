"""Source-neutral extractor model.

An extractor names one raw evidence table and declares how to acquire it
as an ordered tuple of strategies:

- :class:`GlobalQuery` — one query over a deployment-wide system catalog
  (Snowflake ACCOUNT_USAGE, Redshift SVV_*, BigQuery region-level
  INFORMATION_SCHEMA). May be gated behind a profile.
- :class:`PerDatabaseQuery` — the same evidence walked one database at a
  time with ``{database}`` substituted (Snowflake per-database
  INFORMATION_SCHEMA, Postgres catalogs). Partial coverage is recorded per
  database.
- :class:`Command` — a client-materialized command with server-defined
  columns (Snowflake ``SHOW``). Its handoff allowlist is declared
  explicitly because no SELECT projection exists to derive it from.

The runner tries strategies in declared order: a strategy that is
unavailable (privilege/edition gap, classified by the adapter) falls
through to the next; a real failure stops the extractor with ``failed``.

The extractor version recorded in ``meta.extract_runs`` is a content hash
of the strategies' SQL text, so query edits are visible in collection
provenance without manual version bookkeeping.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Union

from ..privacy import PrivacyClass


class Profile(IntEnum):
    """Collection profiles, ordered by privilege requirements.

    Two tiers only (decision 17, 2026-08-19): 'full' was folded into
    'standard'. The neutral meaning: ``lite`` is what any role can read
    without deployment-wide catalog grants; ``standard`` is the complete
    assessment. Each adapter decides which extractors and strategies
    belong to which tier.
    """

    LITE = 1
    STANDARD = 2

    @classmethod
    def parse(cls, name: str) -> "Profile":
        if name.lower() == "full":
            raise ValueError(
                "profile 'full' was folded into 'standard' (decision 17): "
                "a standard collection now includes the workload aggregates. "
                "Re-run with --profile standard (an existing 'full' database "
                "must be re-collected)."
            )
        try:
            return cls[name.upper()]
        except KeyError:
            raise ValueError(f"unknown profile {name!r}; use lite|standard") from None


@dataclass(frozen=True)
class GlobalQuery:
    #: SQL resource path, relative to the adapter's query root
    sql: str
    #: value recorded in meta.extract_runs.source_used
    label: str
    #: the strategy is skipped (not failed) below this profile
    min_profile: Profile = Profile.LITE
    #: the system view/table the extract reads when it differs from the
    #: extract name (provenance; several extracts may read one view)
    source_view: str | None = None


@dataclass(frozen=True)
class PerDatabaseQuery:
    #: SQL resource path with ``{database}`` / ``{database_literal}``
    sql: str
    label: str


@dataclass(frozen=True)
class Command:
    command: str
    #: explicit handoff allowlist: server-defined output has no projection
    expected_columns: tuple[str, ...]
    label: str


SourceStrategy = Union[GlobalQuery, PerDatabaseQuery, Command]


@dataclass(frozen=True)
class Extractor:
    name: str
    category: str  # 'catalog' | 'sizing' | 'features' | 'workload'
    min_profile: Profile
    sources: tuple[SourceStrategy, ...]
    #: raw-table columns usable for scope filtering, keyed by level
    #: ("database" / "schema"), e.g. {"database": "table_catalog"}
    scope_columns: dict[str, str] = field(default_factory=dict)
    #: least privilege that satisfies the extract (free text, adapter
    #: vocabulary; surfaced in README and meta.extract_runs)
    required_privilege: str = ""
    #: minimum product tier/edition, adapter vocabulary
    min_edition: str = ""
    sensitive_fields: dict[str, PrivacyClass] = field(default_factory=dict)
    #: for time-windowed extracts: default lookback, substituted as {window_days}
    window_days: int | None = None
    #: workload time-series extracts take their window from --history-days
    window_from_history_days: bool = False
    #: minutes the extract deliberately stops short of "now" (a source
    #: latency watermark baked into the SQL), disclosed via
    #: meta.extract_runs.actual_window_end
    window_end_lag_minutes: int = 0

    def __post_init__(self) -> None:
        if not self.sources:
            raise ValueError(f"extractor {self.name!r} declares no source strategy")
        if self.command is not None and len(self.sources) != 1:
            raise ValueError(
                f"extractor {self.name!r}: a Command strategy is exclusive"
            )

    @property
    def target_table(self) -> str:
        return self.name

    @property
    def global_query(self) -> GlobalQuery | None:
        return next((s for s in self.sources if isinstance(s, GlobalQuery)), None)

    @property
    def per_database_query(self) -> PerDatabaseQuery | None:
        return next((s for s in self.sources if isinstance(s, PerDatabaseQuery)), None)

    @property
    def command(self) -> Command | None:
        return next((s for s in self.sources if isinstance(s, Command)), None)


def extractor_version(ex: Extractor, load_sql: Callable[[str], str]) -> str:
    """Short content hash over the extractor's SQL text(s) and command."""
    h = hashlib.sha256()
    for s in ex.sources:
        if isinstance(s, Command):
            h.update(s.command.encode())
            h.update(",".join(s.expected_columns).encode())
        else:
            h.update(load_sql(s.sql).encode())
    return h.hexdigest()[:12]


def extractors_for(extractors: list[Extractor], profile: Profile) -> list[Extractor]:
    return [e for e in extractors if e.min_profile <= profile]
