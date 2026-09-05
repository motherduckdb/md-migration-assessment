"""md-assess command-line interface."""

from __future__ import annotations

from typing import Optional

import typer

from . import __version__, db
from .collect.extractor import Profile
from .db import open_output
from .sources import SOURCE_KINDS, get_adapter

DEFAULT_SOURCE = "snowflake"

app = typer.Typer(
    name="md-assess",
    help="MotherDuck migration assessment: inventory a source warehouse into DuckDB.",
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
    source: Optional[str] = typer.Option(
        None, "--source",
        help=f"Source warehouse kind: {' | '.join(SOURCE_KINDS)} "
        f"(default {DEFAULT_SOURCE}). Connection settings come from that "
        "source's environment variables. With --resume the stored "
        "collection's source is used; passing a different one is an error.",
    ),
    profile: str = typer.Option("standard", help="lite | standard"),
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
    """Collect a source-warehouse inventory into a local DuckDB database.

    Safe to interrupt: on Ctrl+C every extractor's state is recorded in
    meta.extract_runs and the database remains a valid partial collection;
    re-run with --resume to continue where it stopped.
    """
    from .collect.runner import Scope, run_collection
    from .report import build_report

    prof = Profile.parse(profile)
    if resume and (scope or profile != "standard" or history_days != 30):
        typer.echo(
            "note: --resume continues the existing collection; profile, "
            "--scope, and --history-days flags are ignored in favor of the "
            "stored collection parameters",
            err=True,
        )

    def progress(msg: str) -> None:
        typer.echo(msg, err=True)

    con = open_output(output)
    # Resolve the adapter from LOCAL state before any warehouse connection
    # is opened: a resume continues the stored collection's source, so it
    # must never connect to the default (or any other) source first.
    try:
        if resume:
            stored = db.load_collection(con)
            if source is not None and source.lower() != stored.source_kind:
                raise ValueError(
                    f"--source {source!r} conflicts with the existing collection "
                    f"(source {stored.source_kind!r}); --resume continues the "
                    "stored source, so omit --source"
                )
            adapter = get_adapter(stored.source_kind)
        else:
            adapter = get_adapter(source or DEFAULT_SOURCE)
        parsed_scope = Scope.parse(scope, adapter.scope)
    except ValueError as exc:
        con.close()
        raise typer.BadParameter(str(exc)) from None
    conn = adapter.open()
    try:
        try:
            coll = run_collection(
                con,
                adapter,
                conn,
                profile=prof,
                scope=parsed_scope,
                history_days=history_days,
                progress=progress,
                resume=resume,
            )
        except KeyboardInterrupt:
            # The runner repairs coverage on the way out; make the partial
            # database immediately useful, then VERIFY the guarantee before
            # claiming it (a second Ctrl+C can interrupt the repair itself).
            try:
                build_report(con)
            except Exception:  # noqa: BLE001 — best effort on the way out
                pass
            try:
                covered = con.execute(
                    "SELECT count(DISTINCT extractor) FROM meta.extract_runs"
                ).fetchone()[0]
                finished = con.execute(
                    "SELECT finished_at FROM meta.collections"
                ).fetchone()[0]
                # consistent interrupted state = full coverage rows AND the
                # collection visibly unfinished (a second Ctrl+C can land
                # inside the repair and leave either half undone)
                complete_coverage = (
                    covered == len(adapter.extractors) and finished is None
                )
            except Exception:  # noqa: BLE001
                complete_coverage = False
            if complete_coverage:
                state = (
                    "Every extractor's state is in meta.extract_runs "
                    "(status 'interrupted' = not collected)."
                )
            else:
                state = (
                    "Coverage rows may be incomplete (the interrupt landed "
                    "inside state recording); meta.extract_runs holds what "
                    "was captured."
                )
            typer.echo(
                f"\ninterrupted — partial collection saved to {output}. "
                f"{state} Continue with:\n"
                f"  md-assess collect --output {output} --resume",
                err=True,
            )
            raise typer.Exit(130) from None
        build_report(con)
    finally:
        conn.close()
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
    dest: str = typer.Option(..., help="Path for the reduced handoff database."),
) -> None:
    """Build a reduced handoff for review (no source bodies or query text)."""
    import json

    from .handoff import build_handoff

    manifest = build_handoff(db, dest)
    typer.echo(json.dumps(manifest, indent=2))
    typer.echo(f"\nreduced handoff written to {dest} (mode 0600); review before sharing")


@app.command()
def report(
    db: str = typer.Option("assessment.duckdb", help="Assessment database path."),
) -> None:
    """Print a coverage summary for the most recent collection."""
    import duckdb

    con = duckdb.connect(db, read_only=True)
    try:
        try:
            from .db import check_meta_version

            check_meta_version(con)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from None
        coll = con.execute(
            """
            SELECT collection_id, profile, mode, source_kind, source_deployment,
                   started_at, finished_at
            FROM meta.collections ORDER BY started_at DESC LIMIT 1
            """
        ).fetchone()
        if coll is None:
            typer.echo("no collections found")
            raise typer.Exit(1)
        cid, profile, mode, kind, deployment, started, finished = coll
        typer.echo(f"\ncollection {cid}")
        typer.echo(f"  source={kind} deployment={deployment} profile={profile} mode={mode}")
        typer.echo(f"  started={started} finished={finished}\n")

        # Which extracts list only what the role can see, from the adapter's
        # manifest (structural, not note-text matching).
        try:
            from .report import visibility_bound_labels

            bound = visibility_bound_labels(get_adapter(kind))
        except ValueError:
            bound = {}

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
        n_bound = 0
        for name, status, src, n, detail in rows:
            line = f"  {name:<{w}}  {status:<13} {src:<20} {n:>10,} rows"
            if src in bound.get(name, ()) and status in ("complete", "partial"):
                line += "  (role-visible only)"
                n_bound += 1
            if detail and status != "complete":
                line += f"  — {detail[:100]}"
            typer.echo(line)
        typer.echo(
            "\nStatuses: complete/partial/unavailable/failed/not_requested. "
            "Missing evidence is never an observed zero — see meta.gaps."
        )
        if n_bound:
            typer.echo(
                f"\nWARNING: {n_bound} extract(s) marked (role-visible only) list "
                "only objects the collecting role has a privilege on. Their "
                "counts are lower bounds and a zero is reported as unknown, "
                "not as absence. To make them account-wide, collect as a role "
                "that can see the objects (e.g. one holding MANAGE GRANTS)."
            )

        has_features = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'report' AND table_name = 'feature_inventory'"
        ).fetchone()[0]
        if has_features:
            observed = con.execute(
                """
                SELECT category, feature, count, lower_bound
                FROM report.feature_inventory
                WHERE collection_id = ? AND observation_status = 'observed'
                ORDER BY category, count DESC
                """,
                [str(cid)],
            ).fetchall()
            unknown, unknown_visibility = con.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE extract_status = 'complete'
                                        AND source_extractor <> '(not implemented)')
                FROM report.feature_inventory
                WHERE collection_id = ? AND observation_status = 'unknown'
                """,
                [str(cid)],
            ).fetchone()
            typer.echo("\nfeatures observed (facts only — no compatibility judgments):")
            if not observed:
                typer.echo("  none")
            for category, feature, n, lower in observed:
                mark = "  (lower bound)" if lower else ""
                typer.echo(f"  {category:<14} {feature:<32} {n:>8,}{mark}")
            if unknown:
                why = "source extracts incomplete"
                if unknown_visibility:
                    why += (
                        f", or nothing visible to the collecting role "
                        f"({unknown_visibility} of them)"
                    )
                typer.echo(
                    f"  ({unknown} features unknown — {why}; "
                    "see report.feature_inventory.note)"
                )
    finally:
        con.close()


if __name__ == "__main__":
    app()
