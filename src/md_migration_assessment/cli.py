"""md-assess command-line interface."""

from __future__ import annotations

from typing import Optional

import typer

from . import __version__
from .db import open_output
from .collect.manifest import Profile

app = typer.Typer(
    name="md-assess",
    help="Snowflake -> MotherDuck migration assessment.",
    no_args_is_help=True,
)


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
) -> None:
    if version:
        typer.echo(__version__)
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


@app.command()
def collect(
    profile: str = typer.Option("standard", help="lite | standard | full"),
    output: str = typer.Option("assessment.duckdb", help="Local output database path."),
    scope: list[str] = typer.Option(
        None, "--scope", help="Limit collection to DB or DB.SCHEMA (repeatable)."
    ),
    query_text: str = typer.Option(
        "hashed", "--query-text", help="none | hashed | redacted | raw"
    ),
    history_days: int = typer.Option(30, min=1, max=365),
) -> None:
    """Collect a Snowflake inventory into a local DuckDB database."""
    from .collect.runner import Scope, run_collection
    from .collect.snowflake import SnowflakeConfig, SnowflakeSource

    prof = Profile.parse(profile)
    parsed_scope = Scope.parse(scope)
    if query_text != "hashed":
        # M3 implements the other modes; refuse rather than silently ignore.
        raise typer.BadParameter("only --query-text hashed is implemented so far")

    cfg = SnowflakeConfig.from_env()
    con = open_output(output)
    source = SnowflakeSource.open(cfg)
    try:
        coll = run_collection(
            con,
            source,
            profile=prof,
            scope=parsed_scope,
            history_days=history_days,
            query_text_mode=query_text,
        )
    finally:
        source.close()
        con.close()

    typer.echo(f"collection {coll.collection_id} written to {output}")
    report(db=output)


@app.command()
def report(
    db: str = typer.Option("assessment.duckdb", help="Assessment database path."),
) -> None:
    """Print a coverage summary for the most recent collection."""
    import duckdb

    con = duckdb.connect(db, read_only=True)
    try:
        coll = con.execute(
            """
            SELECT collection_id, profile, mode, snowflake_account, started_at, finished_at
            FROM meta.collections ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
        if coll is None:
            typer.echo("no collections found")
            raise typer.Exit(1)
        cid, profile, mode, account, started, finished = coll
        typer.echo(f"\ncollection {cid}")
        typer.echo(f"  profile={profile} mode={mode} account={account}")
        typer.echo(f"  started={started} finished={finished}\n")

        rows = con.execute(
            """
            SELECT extractor, status, coalesce(source_used, '-'),
                   coalesce(rows_written, 0), coalesce(error_detail, '')
            FROM meta.extract_runs WHERE collection_id = ?
            ORDER BY CASE status
                       WHEN 'failed' THEN 0 WHEN 'unavailable' THEN 1
                       WHEN 'partial' THEN 2 WHEN 'complete' THEN 3 ELSE 4
                     END, extractor
            """,
            [str(cid)],
        ).fetchall()
        w = max(len(r[0]) for r in rows)
        for name, status, src, n, detail in rows:
            line = f"  {name:<{w}}  {status:<13} {src:<20} {n:>10,} rows"
            if detail and status != "complete":
                line += f"  — {detail[:100]}"
            typer.echo(line)
        typer.echo(
            "\nStatuses: complete/partial/unavailable/failed/not_requested. "
            "Missing evidence is never an observed zero — see meta.gaps."
        )
    finally:
        con.close()


if __name__ == "__main__":
    app()
