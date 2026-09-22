"""Read queries shared by the CLI and the Streamlit app.

All SQL lives here, never in a page file. Filtering and counting happen in
Postgres: pulling rows into pandas to filter them is the classic Streamlit
performance cliff, and it gets worse as the table grows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple

from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from job_searcher.db.models import Job, JobStatus, Source

#: Rows per page. Kept modest because the description column is wide.
PAGE_SIZE = 50


class JobFilters(NamedTuple):
    """Hashable filter set, so `@st.cache_data` can key on it.

    A mutable dict or dataclass would either fail to hash or hash by identity,
    silently defeating the cache on every rerun.
    """

    query: str = ""
    companies: tuple[str, ...] = ()
    departments: tuple[str, ...] = ()
    posted_within_days: int | None = None
    include_closed: bool = False


@dataclass(frozen=True)
class JobRow:
    id: str
    title: str
    company: str
    location_raw: str | None
    department: str | None
    url: str
    posted_at: datetime | None
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    status: str
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_source: str


def _base_stmt(filters: JobFilters):
    stmt = select(
        Job.id,
        Job.title,
        Job.company,
        Job.location_raw,
        Job.department,
        Job.url,
        Job.posted_at,
        Job.first_seen_at,
        Job.last_seen_at,
        Job.status,
        Job.salary_min,
        Job.salary_max,
        Job.salary_currency,
        Job.salary_source,
    )

    if not filters.include_closed:
        # Matches the partial index ix_jobs_open_posted; dropping this
        # predicate stops that index being used.
        stmt = stmt.where(Job.status == JobStatus.open.value)

    # Cross-source duplicates collapse to the kept row.
    stmt = stmt.where(Job.canonical_job_id.is_(None))

    if filters.query:
        # websearch_to_tsquery gives users quoted phrases and -exclusion for
        # free, and never raises on malformed input the way to_tsquery does.
        tsq = func.websearch_to_tsquery("english", filters.query)
        stmt = stmt.where(Job.search_vector.op("@@")(tsq))

    if filters.companies:
        stmt = stmt.where(Job.company.in_(filters.companies))
    if filters.departments:
        stmt = stmt.where(Job.department.in_(filters.departments))
    if filters.posted_within_days:
        cutoff = datetime.now(UTC) - timedelta(days=filters.posted_within_days)
        stmt = stmt.where(Job.posted_at >= cutoff)

    return stmt


def search_jobs(
    session: Session, filters: JobFilters, *, limit: int = PAGE_SIZE, offset: int = 0
) -> list[JobRow]:
    stmt = _base_stmt(filters)
    if filters.query:
        tsq = func.websearch_to_tsquery("english", filters.query)
        stmt = stmt.order_by(func.ts_rank_cd(Job.search_vector, tsq).desc())
    stmt = stmt.order_by(Job.posted_at.desc().nullslast(), Job.id.desc())
    rows = session.execute(stmt.limit(limit).offset(offset)).all()
    return [JobRow(**{**r._mapping, "id": str(r.id)}) for r in rows]


def count_jobs(session: Session, filters: JobFilters) -> int:
    inner = _base_stmt(filters).subquery()
    return session.execute(select(func.count()).select_from(inner)).scalar_one()


def facet_companies(session: Session) -> list[tuple[str, int]]:
    """Company options with counts, aggregated in SQL.

    Counting in pandas would mean fetching every row to render a dropdown.
    """
    stmt = (
        select(Job.company, func.count())
        .where(Job.status == JobStatus.open.value, Job.canonical_job_id.is_(None))
        .group_by(Job.company)
        .order_by(func.count().desc())
    )
    return [(c, n) for c, n in session.execute(stmt).all()]


def facet_departments(session: Session) -> list[tuple[str, int]]:
    stmt = (
        select(Job.department, func.count())
        .where(
            Job.status == JobStatus.open.value,
            Job.canonical_job_id.is_(None),
            Job.department.is_not(None),
        )
        .group_by(Job.department)
        .order_by(func.count().desc())
    )
    return [(d, n) for d, n in session.execute(stmt).all()]


def salary_coverage(session: Session) -> tuple[int, int]:
    """(with salary, total open).

    The UI must show this next to any salary control: a salary filter
    silently hides every NULL, and users read "no jobs" rather than "most
    jobs don't publish salary".
    """
    return field_coverage(session, "salary_min")


#: Columns a UI is allowed to ask coverage for. A mapping rather than a raw
#: string keeps this an allow-list instead of dynamic SQL.
_COVERAGE_COLUMNS = {
    "posted_at": Job.posted_at,
    "department": Job.department,
    "salary_min": Job.salary_min,
    "seniority": Job.seniority,
    "remote_policy": Job.remote_policy,
    "tech_stack": Job.tech_stack,
}


def field_coverage(session: Session, field: str) -> tuple[int, int]:
    """(rows where `field` is non-null, total open rows).

    Same purpose as `salary_coverage`, generalised: *any* filter on a column
    that can be NULL silently drops the unknowns, and the user reads the
    result as "there are no jobs" rather than "this is not published". Every
    such control needs the count beside it.
    """
    column = _COVERAGE_COLUMNS[field]
    open_only = (Job.status == JobStatus.open.value, Job.canonical_job_id.is_(None))
    total = session.execute(select(func.count()).select_from(Job).where(*open_only)).scalar_one()
    known = session.execute(
        select(func.count()).select_from(Job).where(*open_only, column.is_not(None))
    ).scalar_one()
    return known, total


def source_health(session: Session) -> list[dict]:
    stmt = (
        select(
            Source.id,
            Source.company_name,
            Source.kind,
            Source.board_token,
            Source.enabled,
            Source.consecutive_failures,
            Source.blocked_reason,
            Source.last_run_at,
            func.count(Job.id).label("open_jobs"),
        )
        .outerjoin(
            Job, (Job.source_id == Source.id) & (Job.status == JobStatus.open.value)
        )
        .group_by(Source.id)
        .order_by(Source.id)
    )
    return [dict(r._mapping) for r in session.execute(stmt).all()]


def job_detail(session: Session, job_id: str) -> Job | None:
    return session.get(Job, job_id)


def database_ready(engine: Engine) -> bool:
    """True when the schema is present.

    Lets the UI say "run alembic upgrade head" instead of showing a stack
    trace to someone who simply has not migrated yet.
    """
    try:
        with engine.connect() as conn:
            return (
                conn.execute(text("select to_regclass('public.jobs')")).scalar() is not None
            )
    except Exception:
        return False
