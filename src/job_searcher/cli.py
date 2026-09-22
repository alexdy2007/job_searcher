"""Command line interface.

Every entry point is here. Streamlit is a *view* over the database and never
triggers a scrape -- a UI that scrapes on page load re-fetches every board on
every rerun.
"""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from job_searcher.collect import get_adapter, registered_kinds
from job_searcher.collect.fetch import FetchError, build_client, fetch_json
from job_searcher.config import settings
from job_searcher.db import repo
from job_searcher.db.models import Job, ScrapeRun, Source, SourceKind
from job_searcher.db.session import session_scope
from job_searcher.normalize import slugify
from job_searcher.pipeline.run import scrape as run_scrape

app = typer.Typer(no_args_is_help=True, help="Job listing aggregator.")
sources_app = typer.Typer(no_args_is_help=True, help="Manage scrape sources.")
runs_app = typer.Typer(no_args_is_help=True, help="Inspect scrape runs.")
app.add_typer(sources_app, name="sources")
app.add_typer(runs_app, name="runs")

console = Console()


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
@sources_app.command("probe")
def sources_probe(
    kind: str = typer.Option(..., "--kind", help=f"One of: {', '.join(registered_kinds())}"),
    token: str = typer.Option(..., "--token", help="ATS board token / site slug"),
) -> None:
    """Check a board token returns jobs, before adding it as a source.

    A token that 404s is worse than no source at all: it fails quietly on
    every run.
    """
    try:
        adapter = get_adapter(kind)
    except Exception as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    request = adapter.build_request(token, user_agent=settings.user_agent)
    console.print(f"GET {request.url}")
    try:
        with build_client() as client:
            fetched = fetch_json(client, request)
        listings = list(adapter.parse(fetched.payload, company_name=token))
    except FetchError as exc:
        console.print(f"[red]FAIL[/red] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        console.print(f"[red]FAIL[/red] could not parse: {exc}")
        raise typer.Exit(1) from exc

    if not listings:
        console.print("[yellow]0 jobs[/yellow] - token resolves but the board is empty")
        raise typer.Exit(1)

    company = listings[0].company
    console.print(f"[green]OK[/green] {len(listings)} jobs, company={company!r}")
    for listing in listings[:5]:
        console.print(f"  - {listing.title}  [dim]{listing.location_raw or ''}[/dim]")
    console.print(
        f"\nAdd it with:\n  uv run job-searcher sources add "
        f'--kind {kind} --company "{company}" --token {token}'
    )


@sources_app.command("add")
def sources_add(
    kind: str = typer.Option(..., "--kind"),
    company: str = typer.Option(..., "--company"),
    token: str | None = typer.Option(None, "--token", help="ATS board token"),
    url: str | None = typer.Option(None, "--url", help="Careers page URL"),
    slug: str | None = typer.Option(None, "--slug", help="Defaults to a slug of --company"),
) -> None:
    """Add a source. Adding a company is a row, never new code."""
    try:
        source_kind = SourceKind(kind)
    except ValueError as exc:
        console.print(f"[red]Unknown kind {kind!r}. Try: {', '.join(registered_kinds())}[/red]")
        raise typer.Exit(2) from exc

    if source_kind is not SourceKind.html and not token:
        console.print(f"[red]--token is required for kind={kind}[/red]")
        raise typer.Exit(2)

    with session_scope() as session:
        source = Source(
            kind=source_kind,
            company_name=company,
            company_slug=slug or slugify(company),
            board_token=token,
            url=url,
        )
        session.add(source)
        session.flush()
        console.print(
            f"[green]Added[/green] source id={source.id} "
            f"{source.kind.value}:{source.board_token} ({source.company_name})"
        )


@sources_app.command("list")
def sources_list() -> None:
    """Show every source and its health."""
    with session_scope() as session:
        rows = repo.list_sources(session)
        table = Table(title="Sources")
        for col in ("id", "kind", "company", "token", "enabled", "open jobs", "fails", "last run"):
            table.add_column(col)
        for source in rows:
            table.add_row(
                str(source.id),
                source.kind.value,
                source.company_name,
                source.board_token or "-",
                "yes" if source.enabled else "no",
                str(repo.open_job_count(session, source.id)),
                str(source.consecutive_failures),
                source.last_run_at.strftime("%Y-%m-%d %H:%M") if source.last_run_at else "-",
            )
    console.print(table)


# --------------------------------------------------------------------------
# scrape
# --------------------------------------------------------------------------
@app.command("scrape")
def scrape_command(
    source: list[int] = typer.Option(None, "--source", help="Only these source ids"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse and report, write nothing"),
    force_refresh: bool = typer.Option(
        False, "--force-refresh", help="Ignore ETag/Last-Modified and re-parse everything"
    ),
) -> None:
    """Fetch every enabled source and upsert the results."""
    report = run_scrape(
        source_ids=list(source) if source else None,
        dry_run=dry_run,
        force_refresh=force_refresh,
    )

    if not report.outcomes:
        console.print("[yellow]No enabled sources. Add one with 'sources add'.[/yellow]")
        return

    table = Table(title=f"Run {report.run_id or '(dry run)'} - {report.status}")
    for col in ("source", "company", "status", "found", "new", "updated", "unchanged", "ms"):
        table.add_column(col)
    for o in report.outcomes:
        colour = {"success": "green", "failed": "red", "skipped_304": "cyan"}.get(o.status, "")
        table.add_row(
            str(o.source_id),
            o.company,
            f"[{colour}]{o.status}[/{colour}]" if colour else o.status,
            str(o.found),
            str(o.inserted),
            str(o.updated),
            str(o.unchanged),
            str(o.duration_ms),
        )
    console.print(table)

    totals = report.totals
    console.print(
        f"found={totals['found']} new={totals['inserted']} "
        f"updated={totals['updated']} unchanged={totals['unchanged']} "
        f"failed={totals['failed']}"
    )
    for o in report.outcomes:
        if o.error:
            console.print(f"[red]source {o.source_id}:[/red] {o.error}")

    if report.dry_run:
        console.print("[yellow]dry run - nothing was written[/yellow]")


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------
@runs_app.command("show")
def runs_show(
    last: bool = typer.Option(False, "--last", help="Only the most recent run"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Show recent run history."""
    from sqlalchemy import select

    with session_scope() as session:
        stmt = select(ScrapeRun).order_by(ScrapeRun.id.desc()).limit(1 if last else limit)
        runs = list(session.execute(stmt).scalars())
        table = Table(title="Runs")
        for col in ("id", "status", "trigger", "started", "stats", "cost $"):
            table.add_column(col)
        for run in runs:
            table.add_row(
                str(run.id),
                run.status,
                run.trigger,
                run.started_at.strftime("%Y-%m-%d %H:%M:%S"),
                json.dumps(run.stats or {}),
                f"{run.cost_usd:.4f}",
            )
    console.print(table)


@app.command("stats")
def stats() -> None:
    """Row counts, as a quick sanity check after a scrape."""
    from sqlalchemy import func, select

    with session_scope() as session:
        total = session.execute(select(func.count()).select_from(Job)).scalar_one()
        by_status = session.execute(
            select(Job.status, func.count()).group_by(Job.status)
        ).all()
        companies = session.execute(
            select(Job.company, func.count()).group_by(Job.company).order_by(func.count().desc())
        ).all()

    console.print(f"jobs: {total}")
    for status, count in by_status:
        console.print(f"  {status}: {count}")
    for company, count in companies:
        console.print(f"  {company}: {count}")


if __name__ == "__main__":
    app()
