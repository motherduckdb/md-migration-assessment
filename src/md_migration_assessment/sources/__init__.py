"""Source adapter registry.

One adapter per warehouse kind. ``get_adapter`` is the only way the neutral
core reaches an adapter: the CLI resolves ``--source``, and report/handoff
resolve ``meta.collections.source_kind`` of an existing file.

Adapter modules are imported lazily so that importing the package never
pulls in a warehouse client library — those are optional extras, and their
absence must surface as a clear message at connect time, not as an import
error at startup.
"""

from __future__ import annotations

from importlib import import_module

from .base import Connection, ScopeGrammar, SessionInfo, SourceAdapter

__all__ = [
    "Connection",
    "ScopeGrammar",
    "SessionInfo",
    "SourceAdapter",
    "SOURCE_KINDS",
    "get_adapter",
    "register",
]

#: source kind -> dotted module path exposing an ``ADAPTER`` attribute
_BUILTIN: dict[str, str] = {
    "snowflake": "md_migration_assessment.sources.snowflake",
}

_REGISTERED: dict[str, SourceAdapter] = {}

SOURCE_KINDS: tuple[str, ...] = tuple(sorted(_BUILTIN))


def register(adapter: SourceAdapter) -> None:
    """Register an adapter instance (tests use this for fake sources)."""
    _REGISTERED[adapter.name] = adapter


def unregister(name: str) -> None:
    _REGISTERED.pop(name, None)


def get_adapter(name: str) -> SourceAdapter:
    key = name.lower()
    if key in _REGISTERED:
        return _REGISTERED[key]
    module = _BUILTIN.get(key)
    if module is None:
        known = ", ".join(sorted(set(_BUILTIN) | set(_REGISTERED)))
        raise ValueError(f"unknown source {name!r}; supported sources: {known}")
    adapter = import_module(module).ADAPTER
    _REGISTERED[key] = adapter
    return adapter
