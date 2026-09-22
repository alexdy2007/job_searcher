"""Browse the scraped listings.

Streamlit re-runs this whole script on every keystroke and every widget
change, so the rules here are not stylistic:

* No SQL in this file. Every query is a function in `db/queries.py`, shared
  with the CLI, so the page and the command line can never disagree about
  what "open jobs" means.
* The engine is a `@st.cache_resource` singleton; query results are
  `@st.cache_data`, keyed on the hashable `JobFilters`. A `Session` is
  neither -- it is opened and closed inside a single query call and never
  survives a rerun.
* Filtering, counting and faceting happen in Postgres. Loading 358 rows to
  count them in pandas would work today and fall over at 100k.

Phase 1 is deliberately read-only: there is no "scrape now" button, because a
rerun-per-interaction script is the worst possible place to start a
long-running job.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pandas as pd
import streamlit as st
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from job_searcher.db.queries import (
    PAGE_SIZE,
    JobFilters,
    JobRow,
    count_jobs,
    database_ready,
    facet_companies,
    facet_departments,
    field_coverage,
    salary_coverage,
    search_jobs,
)
from job_searcher.db.session import get_engine, get_session_factory

CACHE_TTL = 60

POSTED_WITHIN_OPTIONS: dict[str, int | None] = {
    "Any time": None,
    "Last 7 days": 7,
    "Last 14 days": 14,
    "Last 30 days": 30,
    "Last 90 days": 90,
}

SEARCH_HELP = (
    'Words are ANDed. `"exact phrase"` in double quotes matches a phrase, '
    "`-word` excludes, and `or` between terms widens the search. "
    "Malformed input is tolerated rather than raising."
)


# --------------------------------------------------------------------------
# Resources and cached queries
# --------------------------------------------------------------------------
@st.cache_resource
def _engine() -> Engine:
    """One engine (and one connection pool) per process, not per rerun."""
    return get_engine()


@st.cache_resource
def _session_factory():
    return get_session_factory()


@contextmanager
def _session() -> Iterator[Session]:
    """Read-only scope. Never cached, never held across a rerun."""
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()


@st.cache_data(ttl=CACHE_TTL)
def load_jobs(filters: JobFilters, offset: int) -> list[JobRow]:
    with _session() as session:
        return search_jobs(session, filters, limit=PAGE_SIZE, offset=offset)


@st.cache_data(ttl=CACHE_TTL)
def load_count(filters: JobFilters) -> int:
    with _session() as session:
        return count_jobs(session, filters)


@st.cache_data(ttl=CACHE_TTL)
def load_companies() -> list[tuple[str, int]]:
    with _session() as session:
        return facet_companies(session)


@st.cache_data(ttl=CACHE_TTL)
def load_departments() -> list[tuple[str, int]]:
    with _session() as session:
        return facet_departments(session)


@st.cache_data(ttl=CACHE_TTL)
def load_salary_coverage() -> tuple[int, int]:
    with _session() as session:
        return salary_coverage(session)


@st.cache_data(ttl=CACHE_TTL)
def load_field_coverage(field: str) -> tuple[int, int]:
    with _session() as session:
        return field_coverage(session, field)


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------
st.set_page_config(page_title="job_searcher", page_icon=":briefcase:", layout="wide")

if not database_ready(_engine()):
    st.title("job_searcher")
    st.error("Cannot reach the jobs table.")
    st.markdown(
        "The database is either not running or not migrated. From the project "
        "root:\n\n"
        "```\n"
        "docker compose up -d db\n"
        "uv run alembic upgrade head\n"
        "```\n\n"
        "Postgres listens on host port **5433** locally. If it is running and "
        "this message persists, check `DATABASE_URL` in `.env`."
    )
    st.stop()


# --- sidebar filters ------------------------------------------------------
company_facets = load_companies()
department_facets = load_departments()

with st.sidebar:
    st.header("Filters")

    company_counts = dict(company_facets)
    companies = st.multiselect(
        "Company",
        options=[name for name, _ in company_facets],
        format_func=lambda name: f"{name} ({company_counts[name]})",
        placeholder="All companies",
    )

    department_counts = dict(department_facets)
    departments = st.multiselect(
        "Department",
        options=[name for name, _ in department_facets],
        format_func=lambda name: f"{name} ({department_counts[name]})",
        placeholder="All departments",
    )
    dept_known, dept_total = load_field_coverage("department")
    if departments and dept_known < dept_total:
        st.caption(
            f"{dept_known:,} of {dept_total:,} open jobs have a department. "
            "Filtering here hides the rest."
        )

    posted_label = st.selectbox("Posted within", options=list(POSTED_WITHIN_OPTIONS))
    posted_within_days = POSTED_WITHIN_OPTIONS[posted_label]
    posted_known, posted_total = load_field_coverage("posted_at")
    if posted_within_days is not None and posted_known < posted_total:
        st.caption(
            f"{posted_known:,} of {posted_total:,} open jobs have a posting "
            "date. Boards that publish no date are hidden by this filter."
        )

    include_closed = st.toggle(
        "Include closed and stale jobs",
        value=False,
        help=(
            "Stale means an HTML source stopped listing the role but we do "
            "not yet trust it as closed."
        ),
    )

    st.divider()
    st.caption("Counts beside each option are open jobs only.")

    salary_known, salary_total = load_salary_coverage()
    st.caption(
        f"Salary data: {salary_known:,} of {salary_total:,} open jobs. "
        "Enrichment (Phase 3) fills this in; until then there is nothing to "
        "filter or sort on, so no salary control is offered."
    )

    st.button("Refresh data", on_click=st.cache_data.clear, width="stretch")


# --- header ---------------------------------------------------------------
st.title("job_searcher")

total_open = load_count(JobFilters())
left, right = st.columns(2)
left.metric("Open jobs", f"{total_open:,}")
right.metric("Companies", f"{len(company_facets):,}")

query = st.text_input(
    "Search",
    placeholder='e.g. "staff engineer" python -manager',
    help=SEARCH_HELP,
)
st.caption(SEARCH_HELP)

filters = JobFilters(
    query=query.strip(),
    companies=tuple(companies),
    departments=tuple(departments),
    posted_within_days=posted_within_days,
    include_closed=include_closed,
)

# Any filter change invalidates the current page number: staying on page 6 of
# a 2-page result set shows an empty table that looks like a bug.
if st.session_state.get("_filters") != filters:
    st.session_state["_filters"] = filters
    st.session_state["page"] = 1

matches = load_count(filters)
page_count = max(1, -(-matches // PAGE_SIZE))
page = min(st.session_state.get("page", 1), page_count)
st.session_state["page"] = page


# --- results --------------------------------------------------------------
if matches == 0:
    if total_open == 0 and not include_closed:
        st.warning("There are no jobs in the database yet.")
        st.markdown("Populate it with:\n\n```\nuv run job-searcher scrape\n```")
    else:
        st.info(
            "No jobs match these filters. Try clearing the search box or "
            "widening the date range in the sidebar."
        )
    st.stop()

rows = load_jobs(filters, (page - 1) * PAGE_SIZE)

frame = pd.DataFrame(
    {
        "Title": [r.title for r in rows],
        "Company": [r.company for r in rows],
        "Location": [r.location_raw or "Not stated" for r in rows],
        "Department": [r.department or "Not stated" for r in rows],
        "Posted": [r.posted_at for r in rows],
        "Status": [str(r.status) for r in rows],
        "Link": [r.url for r in rows],
    }
)
if not include_closed:
    frame = frame.drop(columns=["Status"])

first = (page - 1) * PAGE_SIZE + 1
st.caption(f"Showing {first:,}-{first + len(rows) - 1:,} of {matches:,} matching jobs")

st.dataframe(
    frame,
    hide_index=True,
    column_config={
        "Title": st.column_config.TextColumn(width="large"),
        "Posted": st.column_config.DatetimeColumn(format="YYYY-MM-DD", width="small"),
        "Link": st.column_config.LinkColumn(
            "Listing", display_text="Open", width="small"
        ),
    },
)

prev_col, page_col, next_col = st.columns([1, 2, 1])
if prev_col.button("Previous", disabled=page <= 1, width="stretch"):
    st.session_state["page"] = page - 1
    st.rerun()
page_col.markdown(
    f"<div style='text-align:center;padding-top:0.5rem'>Page {page:,} of "
    f"{page_count:,}</div>",
    unsafe_allow_html=True,
)
if next_col.button("Next", disabled=page >= page_count, width="stretch"):
    st.session_state["page"] = page + 1
    st.rerun()
