"""Persistence for scraped listings.

The upsert here is what makes a scrape idempotent: running the same scrape
twice must insert nothing the second time and cost nothing. Everything else in
the pipeline depends on that property.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from job_searcher import normalize
from job_searcher.collect.base import RawListing
from job_searcher.db.models import Job, JobStatus, Source

CHUNK = 500

#: Columns an incoming scrape is allowed to overwrite. Deliberately excludes
#: first_seen_at (the field users actually care about -- "how long has this
#: been up?") and every enrichment column, which belongs to the enrichment
#: pass and would be wiped by a re-scrape if listed here.
_MUTABLE = (
    "url",
    "canonical_url",
    "apply_url",
    "title",
    "title_normalized",
    "company",
    "location_raw",
    "department",
    "team",
    "employment_type",
    "description_html",
    "description_text",
    "posted_at",
    "posted_at_precision",
    "content_hash",
    "dedup_key",
    "extraction_method",
    "extraction_version",
)


@dataclass
class UpsertResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    seen_external_ids: set[str] = field(default_factory=set)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


def to_row(listing: RawListing, source: Source) -> dict:
    """Flatten a listing into column values, computing the derived keys."""
    return {
        "source_id": source.id,
        "external_id": listing.external_id,
        "url": listing.url,
        "canonical_url": normalize.canonical_url(listing.url),
        "apply_url": listing.apply_url,
        "title": listing.title,
        "title_normalized": normalize.normalize_title(listing.title),
        "company": listing.company,
        "location_raw": listing.location_raw,
        "department": listing.department,
        "team": listing.team,
        "employment_type": listing.employment_type,
        "description_html": listing.description_html,
        "description_text": listing.description_text,
        "posted_at": listing.posted_at,
        "posted_at_precision": listing.posted_at_precision,
        "extraction_method": str(listing.extraction_method),
        "extraction_version": 1,
        "content_hash": normalize.content_hash(
            listing.title,
            listing.location_raw,
            listing.employment_type,
            listing.description_text,
        ),
        "dedup_key": normalize.dedup_key(
            source.company_slug, listing.title, listing.location_raw
        ),
        "status": JobStatus.open.value,
        "seen_count": 1,
        "missed_streak": 0,
    }


def upsert_listings(
    session: Session,
    source: Source,
    listings: list[RawListing],
    *,
    run_id: int | None = None,
) -> UpsertResult:
    """Insert or update listings for one source.

    Rows whose ``content_hash`` is unchanged and which are already open are
    skipped by the conflict predicate -- they need no rewrite of a wide row,
    only a cheap last-seen bump, which happens in a second statement.
    """
    result = UpsertResult()
    if not listings:
        return result

    # A board can legitimately repeat an id across paginated responses;
    # ON CONFLICT cannot see a duplicate inside its own VALUES list, so
    # Postgres would raise "cannot affect row a second time".
    by_id: dict[str, RawListing] = {x.external_id: x for x in listings}
    rows = [to_row(x, source) for x in by_id.values()]
    result.seen_external_ids = set(by_id)

    # Postgres' clock, not Python's. Inserts take last_seen_at from the
    # column's server_default now(), so timing an update with the host
    # clock lets last_seen_at land *before* first_seen_at whenever the
    # host and the database container disagree -- observed here as a 74ms
    # skew. statement_timestamp() also advances within a transaction,
    # which transaction-start now() does not.
    now = func.statement_timestamp()
    touched: set[str] = set()

    for start in range(0, len(rows), CHUNK):
        chunk = rows[start : start + CHUNK]
        stmt = pg_insert(Job).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Job.source_id, Job.external_id],
            set_={
                **{col: stmt.excluded[col] for col in _MUTABLE},
                "last_seen_at": now,
                "last_seen_run_id": run_id,
                "seen_count": Job.seen_count + 1,
                # A listing that comes back is open again. Reusing the row
                # keeps first_seen_at; a new row would lose it.
                "status": JobStatus.open.value,
                "closed_at": None,
                "missed_streak": 0,
            },
            where=(
                (Job.content_hash != stmt.excluded.content_hash)
                | (Job.status != JobStatus.open.value)
            ),
        ).returning(
            Job.external_id,
            # xmax = 0 distinguishes a fresh insert from an update, so the
            # counts come back without a second query.
            literal_column("(xmax = 0)").label("inserted"),
        )

        for external_id, inserted in session.execute(stmt):
            touched.add(external_id)
            if inserted:
                result.inserted += 1
            else:
                result.updated += 1

    # Everything the predicate skipped: unchanged and already open. Bump the
    # bookkeeping without rewriting the description.
    unchanged_ids = sorted(result.seen_external_ids - touched)
    if unchanged_ids:
        session.execute(
            update(Job)
            .where(Job.source_id == source.id, Job.external_id.in_(unchanged_ids))
            .values(
                last_seen_at=now,
                last_seen_run_id=run_id,
                seen_count=Job.seen_count + 1,
                missed_streak=0,
            )
        )
        result.unchanged = len(unchanged_ids)

    return result


def touch_source_jobs(
    session: Session, source_id: int, *, run_id: int | None = None
) -> int:
    """Mark every open job of a source as still present.

    Called when a board returns 304. "Not modified" means the whole inventory
    is unchanged, so every listing is still live -- but nothing was parsed, so
    the normal upsert never runs. Without this, `last_seen_at` goes stale
    while the board is genuinely fine, and the Phase 2 closure sweep would
    eventually mark the entire board closed on the strength of a *successful*
    cache hit.
    """
    result = session.execute(
        update(Job)
        .where(Job.source_id == source_id, Job.status == JobStatus.open.value)
        .values(
            last_seen_at=func.statement_timestamp(),
            last_seen_run_id=run_id,
            seen_count=Job.seen_count + 1,
            missed_streak=0,
        )
    )
    return result.rowcount or 0


def open_job_count(session: Session, source_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(Job).where(Job.status == JobStatus.open)
    if source_id is not None:
        stmt = stmt.where(Job.source_id == source_id)
    return session.execute(stmt).scalar_one()


def get_source(session: Session, source_id: int) -> Source | None:
    return session.get(Source, source_id)


def list_sources(session: Session, *, enabled_only: bool = False) -> list[Source]:
    stmt = select(Source).order_by(Source.id)
    if enabled_only:
        stmt = stmt.where(Source.enabled.is_(True))
    return list(session.execute(stmt).scalars())
