"""Feature-signal model: the public, factual half of the taxonomy.

A signal is a SQL probe over raw evidence returning an observed count and a
sample of affected objects. Signals carry no compatibility rating or migration
effort estimate. Each source adapter ships its own signal list; this module
holds the neutral model and the probe builder they share.

Every signal names its source extractor so the report builder can propagate
observation status from meta.extract_runs: a signal over an extract that was
unavailable is UNKNOWN, never zero.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    name: str
    category: str  # table_layout | types | code | security | platform
    source_extractor: str
    #: SQL with a {cid} placeholder; must return (n BIGINT, sample_objects VARCHAR[]).
    sql: str


@dataclass(frozen=True)
class PlannedSignal:
    """A taxonomy entry the tool does not collect evidence for yet.

    Emitted into report.feature_inventory as ``unknown`` so the factual
    inventory can never look complete while silently omitting a feature
    family (spec: missing evidence is never an observed zero — and an
    unimplemented probe is missing evidence).
    """

    name: str
    category: str
    reason: str


def probe(
    name: str,
    category: str,
    source: str,
    predicate: str,
    obj_expr: str,
    *,
    exclude: str | None = None,
    table: str | None = None,
) -> Signal:
    """Build the standard count-and-sample probe.

    ``exclude`` is an extra predicate that drops the source's own system
    furniture from the count (each adapter knows what that looks like).
    """
    table = table or source
    conds = [f"collection_id = '{{cid}}'", f"({predicate})"]
    if exclude:
        conds.append(exclude)
    where = " AND ".join(conds)
    sql = (
        f'WITH hits AS (SELECT {obj_expr} AS obj FROM raw."{table}" WHERE {where})\n'
        "SELECT (SELECT count(*) FROM hits) AS n,\n"
        "       (SELECT coalesce(list(obj), []) FROM\n"
        "          (SELECT DISTINCT obj FROM hits ORDER BY obj LIMIT 20)) AS sample_objects"
    )
    return Signal(name=name, category=category, source_extractor=source, sql=sql)
