"""Scrape run orchestration.

Phase 1 is synchronous and sequential -- async fan-out and rate limiting
arrive with Phase 2, when there are enough sources for concurrency to matter.
What is already here is the part that is hard to add later: every source's
outcome is recorded, and one source failing never aborts the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from job_searcher.collect import get_adapter
from job_searcher.collect.fetch import FetchError, build_client, fetch_json
from job_searcher.config import settings
from job_searcher.db import repo
from job_searcher.db.models import (
    RunStatus,
    ScrapeRun,
    ScrapeRunSource,
    Source,
    SourceStatus,
)
from job_searcher.db.session import session_scope


@dataclass
class SourceOutcome:
    source_id: int
    company: str
    status: str
    found: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    http_status: int | None = None
    error: str | None = None
    duration_ms: int = 0


@dataclass
class RunReport:
    run_id: int | None
    status: str
    outcomes: list[SourceOutcome] = field(default_factory=list)
    dry_run: bool = False

    @property
    def totals(self) -> dict[str, int]:
        return {
            "sources": len(self.outcomes),
            "found": sum(o.found for o in self.outcomes),
            "inserted": sum(o.inserted for o in self.outcomes),
            "updated": sum(o.updated for o in self.outcomes),
            "unchanged": sum(o.unchanged for o in self.outcomes),
            "failed": sum(1 for o in self.outcomes if o.status == SourceStatus.failed),
        }


def scrape(
    *,
    source_ids: list[int] | None = None,
    dry_run: bool = False,
    force_refresh: bool = False,
    trigger: str = "cli",
) -> RunReport:
    """Fetch every enabled source (or the given ones) and persist the results."""
    with session_scope() as session:
        sources = repo.list_sources(session, enabled_only=True)
        if source_ids:
            wanted = set(source_ids)
            sources = [s for s in sources if s.id in wanted]
        # Detach the values we need so the objects are not used across sessions.
        targets = [
            (s.id, s.kind.value, s.company_name, s.board_token, s.etag, s.last_modified)
            for s in sources
        ]

    if not targets:
        return RunReport(run_id=None, status=RunStatus.success.value, dry_run=dry_run)

    run_id = None
    if not dry_run:
        with session_scope() as session:
            run = ScrapeRun(trigger=trigger, status=RunStatus.running.value)
            session.add(run)
            session.flush()
            run_id = run.id

    outcomes: list[SourceOutcome] = []
    with build_client() as client:
        for source_id, kind, company, token, etag, last_modified in targets:
            outcomes.append(
                _run_one(
                    client,
                    source_id=source_id,
                    kind=kind,
                    company=company,
                    token=token,
                    etag=etag,
                    last_modified=last_modified,
                    run_id=run_id,
                    dry_run=dry_run,
                    force_refresh=force_refresh,
                )
            )

    failed = sum(1 for o in outcomes if o.status == SourceStatus.failed)
    if failed == 0:
        status = RunStatus.success.value
    elif failed == len(outcomes):
        status = RunStatus.failed.value
    else:
        status = RunStatus.partial.value

    if not dry_run and run_id is not None:
        with session_scope() as session:
            run = session.get(ScrapeRun, run_id)
            if run is not None:
                run.finished_at = datetime.now(UTC)
                run.status = status
                run.stats = outcomes_to_stats(outcomes)

    return RunReport(run_id=run_id, status=status, outcomes=outcomes, dry_run=dry_run)


def outcomes_to_stats(outcomes: list[SourceOutcome]) -> dict:
    return {
        "sources": len(outcomes),
        "found": sum(o.found for o in outcomes),
        "inserted": sum(o.inserted for o in outcomes),
        "updated": sum(o.updated for o in outcomes),
        "unchanged": sum(o.unchanged for o in outcomes),
    }


def _run_one(
    client,
    *,
    source_id: int,
    kind: str,
    company: str,
    token: str | None,
    etag: str | None,
    last_modified: str | None,
    run_id: int | None,
    dry_run: bool,
    force_refresh: bool = False,
) -> SourceOutcome:
    """Fetch and persist one source.

    Every exception is caught and recorded. A run over many sources must
    survive one broken board, so nothing here is allowed to propagate.
    """
    started = datetime.now(UTC)
    outcome = SourceOutcome(
        source_id=source_id, company=company, status=SourceStatus.pending.value
    )

    try:
        if not token:
            raise FetchError(f"source {source_id} has no board_token")

        adapter = get_adapter(kind)
        request = adapter.build_request(token, user_agent=settings.user_agent)
        # --force-refresh drops the validators so the full payload is
        # re-parsed; without it a cached board 304s and the upsert path is
        # never exercised.
        fetched = fetch_json(
            client,
            request,
            etag=None if force_refresh else etag,
            last_modified=None if force_refresh else last_modified,
        )
        outcome.http_status = fetched.status

        if fetched.not_modified:
            outcome.status = SourceStatus.skipped_304.value
            # 304 means the whole inventory is unchanged, so every listing is
            # still live. Record that, or a healthy cached board looks like a
            # board whose jobs all vanished.
            if not dry_run:
                with session_scope() as session:
                    outcome.unchanged = repo.touch_source_jobs(
                        session, source_id, run_id=run_id
                    )
                    source = repo.get_source(session, source_id)
                    if source is not None:
                        source.last_run_at = datetime.now(UTC)
                        source.consecutive_failures = 0
            return outcome

        listings = list(adapter.parse(fetched.payload, company_name=company))
        outcome.found = len(listings)

        if dry_run:
            outcome.status = SourceStatus.success.value
            return outcome

        with session_scope() as session:
            source = repo.get_source(session, source_id)
            if source is None:
                raise FetchError(f"source {source_id} disappeared mid-run")
            result = repo.upsert_listings(session, source, listings, run_id=run_id)
            outcome.inserted = result.inserted
            outcome.updated = result.updated
            outcome.unchanged = result.unchanged

            source.last_run_at = datetime.now(UTC)
            source.consecutive_failures = 0
            source.blocked_reason = None
            if fetched.etag:
                source.etag = fetched.etag
            if fetched.last_modified:
                source.last_modified = fetched.last_modified

        outcome.status = SourceStatus.success.value

    except Exception as exc:  # noqa: BLE001 - isolation is the point
        outcome.status = SourceStatus.failed.value
        outcome.error = f"{type(exc).__name__}: {exc}"[:500]
        if isinstance(exc, FetchError) and exc.status:
            outcome.http_status = exc.status
        if not dry_run:
            _record_failure(source_id, exc)

    finally:
        outcome.duration_ms = int(
            (datetime.now(UTC) - started).total_seconds() * 1000
        )
        if not dry_run and run_id is not None:
            _record_outcome(run_id, outcome)

    return outcome


def _record_failure(source_id: int, exc: BaseException) -> None:
    try:
        with session_scope() as session:
            source = repo.get_source(session, source_id)
            if source is not None:
                source.consecutive_failures = (source.consecutive_failures or 0) + 1
                source.last_run_at = datetime.now(UTC)
                if getattr(exc, "status", None) in (401, 403):
                    source.blocked_reason = f"HTTP {exc.status}"
    except Exception:
        pass  # bookkeeping must never mask the original failure


def _record_outcome(run_id: int, outcome: SourceOutcome) -> None:
    try:
        with session_scope() as session:
            session.add(
                ScrapeRunSource(
                    run_id=run_id,
                    source_id=outcome.source_id,
                    status=outcome.status,
                    jobs_found=outcome.found,
                    jobs_new=outcome.inserted,
                    jobs_updated=outcome.updated,
                    http_requests=1,
                    http_status=outcome.http_status,
                    error_class=(outcome.error or "").split(":")[0] or None,
                    error_message=outcome.error,
                    duration_ms=outcome.duration_ms,
                )
            )
    except Exception:
        pass


def seen_source_kinds() -> list[str]:
    from job_searcher.collect import registered_kinds

    return registered_kinds()


__all__ = ["RunReport", "SourceOutcome", "Source", "scrape"]
