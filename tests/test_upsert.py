"""Upsert and idempotency tests.

These run against a real Postgres because the behaviour under test *is* the
Postgres behaviour: ON CONFLICT, the conflict predicate, and the xmax trick
that distinguishes an insert from an update. Each test runs inside a
transaction that is rolled back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from job_searcher.collect.base import RawListing
from job_searcher.db import repo
from job_searcher.db.models import Job, JobStatus, Source, SourceKind

pytestmark = pytest.mark.db


@pytest.fixture
def source(db_session) -> Source:
    src = Source(
        kind=SourceKind.greenhouse,
        company_name="Testco",
        company_slug="testco",
        board_token=f"testco-{datetime.now(UTC).timestamp()}",
    )
    db_session.add(src)
    db_session.flush()
    return src


def _listing(external_id: str = "1", **overrides) -> RawListing:
    data = {
        "external_id": external_id,
        "url": f"https://example.com/jobs/{external_id}",
        "title": "Senior Engineer",
        "company": "Testco",
        "location_raw": "London, UK",
        "description_text": "Build things.",
        "description_html": "<p>Build things.</p>",
        "posted_at": datetime(2026, 1, 1, tzinfo=UTC),
        "posted_at_precision": "second",
    }
    data.update(overrides)
    return RawListing(**data)


def _fetch(db_session, source, external_id="1") -> Job:
    return (
        db_session.query(Job)
        .filter(Job.source_id == source.id, Job.external_id == external_id)
        .one()
    )


def test_first_run_inserts(db_session, source):
    result = repo.upsert_listings(db_session, source, [_listing("1"), _listing("2")])
    assert (result.inserted, result.updated, result.unchanged) == (2, 0, 0)


def test_second_identical_run_inserts_nothing(db_session, source):
    """The property the whole pipeline rests on: re-running a scrape must
    insert nothing and rewrite nothing."""
    listings = [_listing("1"), _listing("2")]
    repo.upsert_listings(db_session, source, listings)
    again = repo.upsert_listings(db_session, source, listings)
    assert (again.inserted, again.updated) == (0, 0)
    assert again.unchanged == 2


def test_unchanged_rows_still_have_last_seen_bumped(db_session, source):
    repo.upsert_listings(db_session, source, [_listing("1")])
    before = _fetch(db_session, source)
    first_seen, last_seen = before.first_seen_at, before.last_seen_at

    repo.upsert_listings(db_session, source, [_listing("1")])
    db_session.expire_all()
    after = _fetch(db_session, source)

    assert after.last_seen_at > last_seen
    assert after.seen_count == 2
    # first_seen_at is the field users actually care about ("how long has this
    # been up?") and must survive every re-scrape.
    assert after.first_seen_at == first_seen


def test_changed_description_updates_the_row(db_session, source):
    repo.upsert_listings(db_session, source, [_listing("1")])
    original = _fetch(db_session, source).content_hash

    result = repo.upsert_listings(
        db_session, source, [_listing("1", description_text="Build other things.")]
    )
    db_session.expire_all()
    updated = _fetch(db_session, source)

    assert (result.inserted, result.updated) == (0, 1)
    assert updated.content_hash != original
    assert updated.description_text == "Build other things."


def test_a_reopened_job_reuses_its_row(db_session, source):
    """Creating a new row for a returning listing would lose first_seen_at."""
    repo.upsert_listings(db_session, source, [_listing("1")])
    job = _fetch(db_session, source)
    original_id, first_seen = job.id, job.first_seen_at
    job.status = JobStatus.closed.value
    job.closed_at = datetime.now(UTC)
    job.missed_streak = 3
    db_session.flush()

    result = repo.upsert_listings(db_session, source, [_listing("1")])
    db_session.expire_all()
    reopened = _fetch(db_session, source)

    assert result.updated == 1 and result.inserted == 0
    assert reopened.id == original_id
    assert reopened.status == JobStatus.open
    assert reopened.closed_at is None
    assert reopened.missed_streak == 0
    assert reopened.first_seen_at == first_seen


def test_duplicate_ids_within_one_batch_do_not_error(db_session, source):
    """ON CONFLICT cannot see a duplicate inside its own VALUES list, so
    Postgres raises "cannot affect row a second time" unless we dedupe first.
    A board repeating an id across pages is not hypothetical."""
    result = repo.upsert_listings(
        db_session,
        source,
        [_listing("1", title="First"), _listing("1", title="Second")],
    )
    assert result.inserted == 1
    assert _fetch(db_session, source).title == "Second"


def test_enrichment_columns_survive_a_rescrape(db_session, source):
    """A re-scrape must not wipe fields the enrichment pass wrote, or every
    scrape would silently discard everything Phase 3 paid for."""
    repo.upsert_listings(db_session, source, [_listing("1")])
    job = _fetch(db_session, source)
    job.salary_min = 100000
    job.seniority = "senior"
    job.tech_stack = ["python"]
    db_session.flush()

    repo.upsert_listings(
        db_session, source, [_listing("1", description_text="Changed.")]
    )
    db_session.expire_all()
    after = _fetch(db_session, source)

    assert after.description_text == "Changed."
    assert after.salary_min == 100000
    assert after.seniority == "senior"
    assert after.tech_stack == ["python"]


def test_touch_marks_every_open_job_as_still_present(db_session, source):
    """What a 304 means: the inventory is unchanged, so nothing vanished.
    Without this a healthy cached board would age towards being closed."""
    repo.upsert_listings(db_session, source, [_listing("1"), _listing("2")])
    old = datetime.now(UTC) - timedelta(days=5)
    for job in db_session.query(Job).filter(Job.source_id == source.id):
        job.last_seen_at = old
        job.missed_streak = 2
    db_session.flush()

    touched = repo.touch_source_jobs(db_session, source.id)
    db_session.expire_all()

    assert touched == 2
    for job in db_session.query(Job).filter(Job.source_id == source.id):
        assert job.last_seen_at > old
        assert job.missed_streak == 0


def test_empty_listing_set_is_a_noop(db_session, source):
    result = repo.upsert_listings(db_session, source, [])
    assert result.total == 0 and result.seen_external_ids == set()


def test_seen_external_ids_are_reported_for_the_closure_sweep(db_session, source):
    """Phase 2's closure sweep needs the set of ids the board actually
    returned."""
    result = repo.upsert_listings(db_session, source, [_listing("1"), _listing("7")])
    assert result.seen_external_ids == {"1", "7"}
