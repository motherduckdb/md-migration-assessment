"""Snowflake connection and Arrow-batch fetching.

The collector's only network peer in local mode. Reads are streamed as Arrow
record batches (bounded memory, no pandas materialization) and handed to
DuckDB for ingestion.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import chain
from typing import Iterable, Iterator

import pyarrow as pa


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


@dataclass
class SessionInfo:
    account: str | None
    version: str | None
    region: str | None
    edition: str | None = None  # not directly exposed by Snowflake


class SnowflakeSource:
    """Live Snowflake data source. Tests substitute a fake with the same shape."""

    def __init__(self, conn) -> None:
        self._conn = conn

    @classmethod
    def open(cls, cfg: SnowflakeConfig) -> "SnowflakeSource":
        import snowflake.connector  # deferred: keep import cost out of tests

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
        return SessionInfo(account=account, version=version, region=region)

    def close(self) -> None:
        self._conn.close()
