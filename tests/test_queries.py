"""Read-query tests.

These guard the invariant that the browse page and every coverage caption
agree about what "open jobs" means. A caption reading "12 of 400" beside a
header reading "358 open jobs" is not a cosmetic mismatch -- it is the UI
telling the user two different things about the same table.

Two deliberate choices:

* Rows are seeded through `repo.upsert_listings`, the same path a scrape
  uses, rather than hand-built `Job()` objects. A fixture that sets columns
  the real writer does not would let a query pass here and fail on scraped
  data.
* Assertions are on *deltas* against a baseline taken before seeding. These
  queries are deliberately global -- the browse page has no source filter --
  and the developer database already holds real scraped jobs, so an absolute
  count would pass only on an empty database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from job_searcher.collect.base import RawListing
from job_searcher.db import queries, repo
from job_searcher.db.models import Job, JobStatus, Source, SourceKind

pytestmark = pytest.mark.db

#: A token that cannot occur in a real listing, so a full-text assertion can
#: be exact even with hundreds of scraped rows in the table.
MARKER = "quokkaflux"


def _listing(external_id: str, title: str, **overrides) -> RawListing:
    data = {
        "external_id": external_id,
        "url": f"https://example.com/jobs/{external_id}",
        "title": title,
        "company": "Testco",
        "location_raw": "London, UK",
        "description_text": f"Work on {title}.",
        "description_html": f"<p>Work on {title}.</p>",
        "posted_at": datetime(2026, 1, 1, tzinfo=UTC),
        "posted_at_precision": "second",
    }
    data.update(overrides)
    return RawListing(**data)


@dataclass
class Seeded:
    source: Source
    browse_total: int
    open_with_department: int
    open_total: int


@pytest.fixture
def seeded(db_session) -> Seeded:
    """Two open jobs, one closed, and one open row that was deduped away."""
    before_browse = queries.count_jobs(db_session, queries.JobFilters())
    before_known, before_total = queries.field_coverage(db_session, "department")

    src = Source(
        kind=SourceKind.greenhouse,
        company_name="Testco",
        company_slug="testco",
        board_token=f"testco-{datetime.now(UTC).timestamp()}",
    )
    db_session.add(src)
    db_session.flush()

    repo.upsert_listings(
        db_session,
        src,
        [
            _listing("1", f"{MARKER} Engineer", department=f"{MARKER} Engineering"),
            _listing("2", f"{MARKER} Designer"),
            _listing("3", f"{MARKER} Analyst"),
            _listing("4", f"{MARKER} Engineer"),
        ],
    )
    db_session.flush()

    by_id = {
        job.external_id: job
        for job in db_session.query(Job).filter(Job.source_id == src.id)
    }
    by_id["3"].status = JobStatus.closed.value
    by_id["3"].closed_at = datetime.now(UTC)
    # Job 4 is the same role seen twice; Phase 5 collapses it onto job 1
    # rather than deleting it, so provenance survives.
    by_id["4"].canonical_job_id = by_id["1"].id
    db_session.flush()

    return Seeded(
        source=src,
        browse_total=before_browse,
        open_with_department=before_known,
        open_total=before_total,
    )


def _browse_total(session) -> int:
    return queries.count_jobs(session, queries.JobFilters())


def test_browse_count_excludes_closed_and_deduped(db_session, seeded):
    """Four rows in, two visible: the closed one and the deduped one are
    both hidden from the default browse."""
    assert _browse_total(db_session) == seeded.browse_total + 2


def test_coverage_denominator_matches_the_browse_count(db_session, seeded):
    """Every coverage caption sits beside the "Open jobs" metric, so its
    denominator has to be the same population the metric counts."""
    for field in ("department", "posted_at", "salary_min"):
        _, total = queries.field_coverage(db_session, field)
        assert total == _browse_total(db_session), field


def test_salary_coverage_agrees_with_field_coverage(db_session, seeded):
    """The two helpers are shown in the same sidebar; they must not report
    different totals for the same table."""
    assert queries.salary_coverage(db_session) == queries.field_coverage(
        db_session, "salary_min"
    )


def test_coverage_counts_only_non_null(db_session, seeded):
    known, total = queries.field_coverage(db_session, "department")
    assert known == seeded.open_with_department + 1
    assert total == seeded.open_total + 2


def test_unknown_coverage_field_is_rejected(db_session):
    """The allow-list is what keeps this from becoming dynamic SQL."""
    with pytest.raises(KeyError):
        queries.field_coverage(db_session, "description_text")


def test_facets_ignore_closed_and_deduped_rows(db_session, seeded):
    assert dict(queries.facet_companies(db_session))["Testco"] == 2
    assert dict(queries.facet_departments(db_session))[f"{MARKER} Engineering"] == 1


def test_search_matches_the_description_not_just_the_title(db_session, seeded):
    """The tsvector covers title, company and description; the closed and
    deduped rows stay hidden even when they match the query."""
    rows = queries.search_jobs(db_session, queries.JobFilters(query=MARKER))
    assert sorted(r.title for r in rows) == [
        f"{MARKER} Designer",
        f"{MARKER} Engineer",
    ]


def test_malformed_search_input_does_not_raise(db_session, seeded):
    """websearch_to_tsquery tolerates what to_tsquery would reject. Users type
    stray quotes and operators constantly."""
    queries.search_jobs(db_session, queries.JobFilters(query='"unclosed & |'))


def test_include_closed_widens_the_result(db_session, seeded):
    closed_included = queries.count_jobs(
        db_session, queries.JobFilters(include_closed=True)
    )
    assert closed_included == _browse_total(db_session) + 1
