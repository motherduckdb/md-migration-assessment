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
    history_days: int = typer.Option(30, min=1, max=365),
    resume: bool = typer.Option(
        False,
        "--resume",
        help="Continue an incomplete collection in OUTPUT: extractors already "
        "complete are skipped; profile/scope/history come from the existing "
        "collection, not from these flags.",
    ),
) -> None:
    """Collect a Snowflake inventory into a local DuckDB database.

    Safe to interrupt: on Ctrl+C every extractor's state is recorded in
    meta.extract_runs and the database remains a valid partial collection;
    re-run with --resume to continue where it stopped.
    """
    from .collect.runner import Scope, run_collection
    from .collect.snowflake import SnowflakeConfig, SnowflakeSource
    from .report import build_report

    prof = Profile.parse(profile)
    parsed_scope = Scope.parse(scope)
    if resume and (scope or profile != "standard" or history_days != 30):
        typer.echo(
            "note: --resume continues the existing collection; profile, "
            "--scope, and --history-days flags are ignored in favor of the "
            "stored collection parameters",
            err=True,
        )

    def progress(msg: str) -> None:
        typer.echo(msg, err=True)

    cfg = SnowflakeConfig.from_env()
    con = open_output(output)
    source = SnowflakeSource.open(cfg)
    try:
        try:
            coll = run_collection(
                con,
                source,
                profile=prof,
                scope=parsed_scope,
                history_days=history_days,
                progress=progress,
                resume=resume,
            )
        except KeyboardInterrupt:
            # The runner already recorded honest coverage rows; make the
            # partial database immediately useful before exiting.
            try:
                build_report(con)
            except Exception:  # noqa: BLE001 — best effort on the way out
                pass
            typer.echo(
                f"\ninterrupted — partial collection saved to {output}. "
                "Every extractor's state is in meta.extract_runs "
                "(status 'interrupted' = not collected). Continue with:\n"
                f"  md-assess collect --output {output} --resume",
                err=True,
            )
            raise typer.Exit(130) from None
        build_report(con)
    finally:
        source.close()
        con.close()

    typer.echo(f"collection {coll.collection_id} written to {output}")
    report(db=output)


@app.command()
def assess(
    db: str = typer.Option("assessment.duckdb", help="Assessment database path."),
) -> None:
    """(Re)build the factual report.* views on an existing collection."""
    import duckdb

    from .report import build_report

    con = duckdb.connect(db)
    try:
        summary = build_report(con)
    finally:
        con.close()
    typer.echo(
        f"report built: {summary['collections']} collection(s), "
        f"{summary['features']} feature rows ({summary['unknown']} unknown)"
    )


@app.command()
def handoff(
    db: str = typer.Option("assessment.duckdb", help="Assessment database path."),
    dest: str = typer.Option(..., help="Path for the sanitized handoff database."),
) -> None:
    """Build a sanitized handoff database (no source bodies, no query text)."""
    import json

    from .handoff import build_handoff

    manifest = build_handoff(db, dest)
    typer.echo(json.dumps(manifest, indent=2))
    typer.echo(f"\nsanitized handoff written to {dest} (mode 0600)")


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

        has_features = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'report' AND table_name = 'feature_inventory'"
        ).fetchone()[0]
        if has_features:
            observed = con.execute(
                """
                SELECT category, feature, count FROM report.feature_inventory
                WHERE collection_id = ? AND observation_status = 'observed'
                ORDER BY category, count DESC
                """,
                [str(cid)],
            ).fetchall()
            unknown = con.execute(
                "SELECT count(*) FROM report.feature_inventory "
                "WHERE collection_id = ? AND observation_status = 'unknown'",
                [str(cid)],
            ).fetchone()[0]
            typer.echo("\nfeatures observed (facts only — no compatibility judgments):")
            if not observed:
                typer.echo("  none")
            for category, feature, n in observed:
                typer.echo(f"  {category:<14} {feature:<32} {n:>8,}")
            if unknown:
                typer.echo(
                    f"  ({unknown} features unknown — source extracts incomplete; "
                    "see report.feature_inventory)"
                )
    finally:
        con.close()


if __name__ == "__main__":
    app()
