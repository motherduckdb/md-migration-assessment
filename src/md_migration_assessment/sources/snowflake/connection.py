"""Snowflake connection and Arrow-batch fetching.

The collector's only network peer in local mode. Reads are streamed as Arrow
record batches (bounded memory, no pandas materialization) and handed to
DuckDB for ingestion. :class:`SnowflakeSource` implements the neutral
:class:`~md_migration_assessment.sources.base.Connection` protocol.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import chain
from typing import Iterable, Iterator

import pyarrow as pa

from ..base import SessionInfo


def as_record_batches(
    items: Iterable["pa.Table | pa.RecordBatch"],
) -> Iterator[pa.RecordBatch]:
    """Normalize the connector's arrow stream to RecordBatches.

    snowflake-connector-python's fetch_arrow_batches() yields pyarrow.Table
    objects (one per result chunk), not RecordBatches; DuckDB's arrow scan
    requires RecordBatches and fails at consumption time otherwise.
    """
    for item in items:
        if isinstance(item, pa.Table):
            yield from item.to_batches()
        else:
            yield item

#: System databases never walked by INFORMATION_SCHEMA fallbacks.
EXCLUDED_DATABASES = {"SNOWFLAKE", "SNOWFLAKE_SAMPLE_DATA"}


def ensure_typed(table: pa.Table) -> pa.Table:
    """Replace null-typed columns with string columns.

    An empty (or all-NULL) SHOW result makes Arrow infer the null type, which
    DuckDB then binds as a non-text column and string predicates in report
    probes fail (found live: SHOW STREAMLITS with zero rows)."""
    if not any(pa.types.is_null(f.type) for f in table.schema):
        return table
    fields = [
        pa.field(f.name, pa.string()) if pa.types.is_null(f.type) else f
        for f in table.schema
    ]
    return table.cast(pa.schema(fields))


@dataclass
class SnowflakeConfig:
    account: str
    user: str
    password: str | None = None
    private_key_path: str | None = None
    private_key_passphrase: str | None = None
    role: str | None = None
    warehouse: str | None = None
    authenticator: str | None = None

    @classmethod
    def from_env(cls) -> "SnowflakeConfig":
        env = os.environ
        account = env.get("SNOWFLAKE_ACCOUNT")
        user = env.get("SNOWFLAKE_USER")
        if not account or not user:
            raise ValueError("SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER are required")
        cfg = cls(
            account=account,
            user=user,
            password=env.get("SNOWFLAKE_PASSWORD"),
            private_key_path=env.get("SNOWFLAKE_PRIVATE_KEY_PATH"),
            private_key_passphrase=env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
            role=env.get("SNOWFLAKE_ROLE"),
            warehouse=env.get("SNOWFLAKE_WAREHOUSE"),
            authenticator=env.get("SNOWFLAKE_AUTHENTICATOR"),
        )
        if not cfg.password and not cfg.private_key_path and not cfg.authenticator:
            raise ValueError(
                "set SNOWFLAKE_PASSWORD, SNOWFLAKE_PRIVATE_KEY_PATH, "
                "or SNOWFLAKE_AUTHENTICATOR"
            )
        return cfg

    def connect_kwargs(self) -> dict:
        kwargs: dict = {
            "account": self.account,
            "user": self.user,
            # The collector only reads; a session-level guard costs nothing.
            "session_parameters": {"QUERY_TAG": "md-migration-assessment"},
        }
        if self.password:
            kwargs["password"] = self.password
        if self.private_key_path:
            kwargs["private_key_file"] = self.private_key_path
            if self.private_key_passphrase:
                kwargs["private_key_file_pwd"] = self.private_key_passphrase.encode()
        if self.authenticator:
            kwargs["authenticator"] = self.authenticator
        if self.role:
            kwargs["role"] = self.role
        if self.warehouse:
            kwargs["warehouse"] = self.warehouse
        return kwargs


class SnowflakeSource:
    """Live Snowflake connection. Tests substitute a fake with the same shape."""

    def __init__(self, conn) -> None:
        self._conn = conn

    @classmethod
    def open(cls, cfg: SnowflakeConfig) -> "SnowflakeSource":
        try:
            import snowflake.connector  # deferred: an optional extra
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise ImportError(
                "the Snowflake connector is not installed; install the "
                "'snowflake' extra: pip install 'md-migration-assessment[snowflake]'"
            ) from exc

        return cls(snowflake.connector.connect(**cfg.connect_kwargs()))

    def reader(self, sql: str) -> pa.RecordBatchReader | pa.Table:
        cur = self._conn.cursor()
        cur.execute(sql)
        batches = cur.fetch_arrow_batches()
        try:
            first = next(batches)
        except StopIteration:
            # Empty result: re-run and let the connector build an empty table
            # with the correct schema.
            cur = self._conn.cursor()
            cur.execute(sql)
            return cur.fetch_arrow_all(force_return_table=True)

        return pa.RecordBatchReader.from_batches(
            first.schema, as_record_batches(chain([first], batches))
        )

    def command(self, command: str) -> tuple[pa.Table, bool]:
        """Run a SHOW command; returns (arrow table, truncated?).

        SHOW output is not fetchable as Arrow, so rows are materialized and
        converted; SHOW caps output at 10,000 rows, and hitting the cap is
        reported so the runner can record partial coverage instead of
        presenting a truncated inventory as complete.
        """
        cur = self._conn.cursor()
        cur.execute(command)
        rows = cur.fetchall()
        names = [d[0] for d in cur.description]
        columns = {name: [row[i] for row in rows] for i, name in enumerate(names)}
        try:
            table = pa.table(columns)
        except (pa.ArrowInvalid, pa.ArrowTypeError):
            # mixed types the inference can't unify: stringify (evidence
            # fidelity beats a failed extract)
            table = pa.table(
                {
                    name: [None if v is None else str(v) for v in vals]
                    for name, vals in columns.items()
                }
            )
        return ensure_typed(table), len(rows) >= 10_000

    def list_databases(self) -> list[str]:
        cur = self._conn.cursor()
        cur.execute("SHOW TERSE DATABASES")
        # 'name' is the second column of SHOW TERSE DATABASES output.
        names = [row[1] for row in cur.fetchall()]
        return [n for n in names if n.upper() not in EXCLUDED_DATABASES]

    def session_info(self) -> SessionInfo:
        cur = self._conn.cursor()
        cur.execute(
            "SELECT current_account_name(), current_version(), current_region()"
        )
        account, version, region = cur.fetchone()
        # edition is not directly exposed by Snowflake
        return SessionInfo(deployment=account, version=version, region=region)

    def server_time(self) -> datetime:
        """Snowflake's own clock, as a UTC-aware datetime.

        Coverage-window metadata must be anchored to the server clock:
        client/server skew means a client timestamp captured before an
        extract runs can still exceed the SQL's CURRENT_TIMESTAMP.
        """
        cur = self._conn.cursor()
        cur.execute("SELECT current_timestamp()")
        (ts,) = cur.fetchone()
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)

    def close(self) -> None:
        self._conn.close()
