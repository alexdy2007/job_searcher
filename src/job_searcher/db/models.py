"""SQLAlchemy 2.0 models.

Design notes that are easy to lose:

* A listing's identity is ``(source_id, external_id)``. That pair is unique, so
  re-running a scrape upserts rather than duplicating.
* ``content_hash`` is the cost gate. Enrichment only runs when it changes, so a
  daily re-scrape of thousands of unchanged listings costs nothing.
* ``dedup_key`` *groups* the same role seen through two sources. It is
  deliberately not unique, and the loser is linked via ``canonical_job_id``
  rather than deleted -- provenance is worth more than a tidy table, and the
  heuristic will sometimes be wrong.

Enum policy: native Postgres enums only for ``job_status`` and ``source_kind``,
which are stable. Everything churn-prone (seniority, remote policy, extraction
method) is ``text`` plus a CHECK constraint, because ``ALTER TYPE ... ADD
VALUE`` cannot run inside a transaction and makes Alembic downgrades
effectively impossible. Adding a seniority level should be a one-line CHECK
replacement, not a data migration. The Python StrEnums below remain the source
of the allowed values.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from job_searcher.db.base import Base


# --------------------------------------------------------------------------
# Value vocabularies
# --------------------------------------------------------------------------
class SourceKind(enum.StrEnum):
    greenhouse = "greenhouse"
    lever = "lever"
    ashby = "ashby"
    workable = "workable"
    html = "html"


class JobStatus(enum.StrEnum):
    open = "open"
    #: HTML sources only: missing from recent runs, but not yet trusted as
    #: closed. Shown in the UI with a warning rather than hidden.
    stale = "stale"
    closed = "closed"


class ExtractionMethod(enum.StrEnum):
    ats = "ats"
    jsonld = "jsonld"
    llm_html = "llm_html"
    llm_enrich = "llm_enrich"


class RemotePolicy(enum.StrEnum):
    onsite = "onsite"
    hybrid = "hybrid"
    remote_country = "remote_country"
    remote_region = "remote_region"
    remote_global = "remote_global"
    unknown = "unknown"


class Seniority(enum.StrEnum):
    intern = "intern"
    junior = "junior"
    mid = "mid"
    senior = "senior"
    staff = "staff"
    principal = "principal"
    lead = "lead"
    manager = "manager"
    director = "director"
    executive = "executive"
    unknown = "unknown"


class SalaryPeriod(enum.StrEnum):
    hour = "hour"
    day = "day"
    week = "week"
    month = "month"
    year = "year"


class SalarySource(enum.StrEnum):
    """Where a salary figure came from. Surfaced in the UI, because a
    model-inferred range deserves less trust than one the ATS published."""

    ats = "ats"
    regex = "regex"
    llm = "llm"
    none = "none"


class RunStatus(enum.StrEnum):
    running = "running"
    success = "success"
    partial = "partial"
    failed = "failed"
    budget_exceeded = "budget_exceeded"


class SourceStatus(enum.StrEnum):
    pending = "pending"
    success = "success"
    failed = "failed"
    #: Fetched fine, but the result failed the volume/null-rate sanity gate.
    #: The closure sweep is skipped for this source.
    suspect_drift = "suspect_drift"
    skipped_304 = "skipped_304"
    skipped_robots = "skipped_robots"


def _check(column: str, py_enum: type[enum.StrEnum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{m.value}'" for m in py_enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


def _check_nullable(column: str, py_enum: type[enum.StrEnum], name: str) -> CheckConstraint:
    values = ", ".join(f"'{m.value}'" for m in py_enum)
    return CheckConstraint(f"{column} IS NULL OR {column} IN ({values})", name=name)


def _pg_enum(py_enum: type[enum.Enum], name: str) -> Enum:
    # values_callable keeps the DB labels equal to the enum *values*, not the
    # Python member names; without it the two drift the moment a value differs
    # from its name.
    return Enum(py_enum, name=name, values_callable=lambda e: [m.value for m in e])


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
class Source(Base):
    """One company board. Adding a company is a row here, never new code."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[SourceKind] = mapped_column(_pg_enum(SourceKind, "source_kind"))
    company_name: Mapped[str] = mapped_column(String(200))
    company_slug: Mapped[str] = mapped_column(String(200), index=True)

    #: ATS identifier: Greenhouse board token, Lever site slug, Ashby org name,
    #: Workable subdomain. Null for ``kind='html'``.
    board_token: Mapped[str | None] = mapped_column(String(200))

    #: Careers page URL. The fetch target for ``kind='html'``; informational
    #: for ATS sources, and the input to ATS re-detection.
    url: Mapped[str | None] = mapped_column(Text)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")

    #: Per-source overrides: CSS selectors, pagination, rate limit.
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    #: 'auto' | 'http' | 'browser'. Set to 'browser' after a successful
    #: Playwright escalation so later runs skip the wasted httpx attempt.
    render_mode: Mapped[str] = mapped_column(String(20), default="auto", server_default="auto")

    #: Conditional GET state. A 304 means no parse, no LLM, no cost.
    etag: Mapped[str | None] = mapped_column(String(300))
    last_modified: Mapped[str | None] = mapped_column(String(100))

    rate_limit_per_min: Mapped[int] = mapped_column(
        SmallInteger, default=30, server_default="30"
    )
    consecutive_failures: Mapped[int] = mapped_column(
        SmallInteger, default=0, server_default="0"
    )
    #: Set when robots.txt disallows us or the site serves a bot challenge.
    #: Surfaced in the UI instead of being silently retried forever.
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    jobs: Mapped[list[Job]] = relationship(back_populates="source", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("kind", "board_token", name="uq_sources_kind_board_token"),
        CheckConstraint("render_mode IN ('auto', 'http', 'browser')", name="render_mode"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))

    #: ATS job id, or sha256 of the canonical URL for HTML sources.
    external_id: Mapped[str] = mapped_column(String(255))

    # --- deterministic: filled by the adapter, never by the LLM ---
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str | None] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    title_normalized: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str] = mapped_column(String(200))
    location_raw: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(String(200))
    team: Mapped[str | None] = mapped_column(String(200))
    employment_type: Mapped[str | None] = mapped_column(String(100))
    description_html: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: 'second' | 'day' | 'unknown'. ATS feeds are frequently date-only, and
    #: without this the UI would claim "posted 3 hours ago" from a date.
    posted_at_precision: Mapped[str | None] = mapped_column(String(10))

    # --- enriched: every field nullable, because "absent" must be sayable ---
    salary_min: Mapped[Decimal | None] = mapped_column()
    salary_max: Mapped[Decimal | None] = mapped_column()
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str | None] = mapped_column(String(10))
    salary_source: Mapped[str] = mapped_column(String(10), default="none", server_default="none")
    seniority: Mapped[str | None] = mapped_column(String(20))
    remote_policy: Mapped[str | None] = mapped_column(String(20))
    remote_regions: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    tech_stack: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    years_experience_min: Mapped[int | None] = mapped_column(SmallInteger)
    years_experience_max: Mapped[int | None] = mapped_column(SmallInteger)
    location_city: Mapped[str | None] = mapped_column(String(200))
    location_region: Mapped[str | None] = mapped_column(String(200))
    location_country: Mapped[str | None] = mapped_column(String(100))
    #: Tri-state: True / False / None-for-unstated.
    visa_sponsorship: Mapped[bool | None] = mapped_column(Boolean)

    #: Off-vocabulary values, low-confidence notes, and the truncation flag set
    #: when a page had to be trimmed to fit the token budget. Anything the
    #: model returned that we would not stand behind lands here, not in a
    #: typed column.
    enrichment_flags: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    # --- lifecycle ---
    status: Mapped[JobStatus] = mapped_column(
        _pg_enum(JobStatus, "job_status"), default=JobStatus.open, server_default="open"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_run_id: Mapped[int | None] = mapped_column(BigInteger)
    seen_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    #: Consecutive successful runs in which this listing was absent. HTML
    #: sources cannot guarantee a complete inventory, so they age out through
    #: ``stale`` rather than closing on a single miss.
    missed_streak: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- provenance ---
    extraction_method: Mapped[str] = mapped_column(String(20))
    extraction_model: Mapped[str | None] = mapped_column(String(100))
    extraction_version: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    enrichment_version: Mapped[int | None] = mapped_column(SmallInteger)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: sha256 over normalised title + location + description. Enrichment
    #: re-runs only when this moves.
    content_hash: Mapped[str] = mapped_column(String(64))

    #: Normalised company|title|location bucket. Groups the same role across
    #: sources; not unique.
    dedup_key: Mapped[str] = mapped_column(String(64))

    #: Set on the *losing* row of a dedup group, pointing at the kept one.
    #: The browse view filters on ``canonical_job_id IS NULL``, so grouping is
    #: reversible by nulling this column.
    canonical_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )

    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            # left(..., 100000) is load-bearing: tsvector has a 1MB ceiling and
            # a handful of career pages will otherwise fail on INSERT.
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(company, '')), 'B') || "
            "setweight(to_tsvector('english', "
            "coalesce(left(description_text, 100000), '')), 'C')",
            persisted=True,
        ),
    )

    source: Mapped[Source] = relationship(back_populates="jobs")

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_jobs_source_external"),
        _check("extraction_method", ExtractionMethod, "extraction_method"),
        _check("salary_source", SalarySource, "salary_source"),
        _check_nullable("seniority", Seniority, "seniority"),
        _check_nullable("remote_policy", RemotePolicy, "remote_policy"),
        _check_nullable("salary_period", SalaryPeriod, "salary_period"),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_min <= salary_max",
            name="salary_range",
        ),
        Index("ix_jobs_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_jobs_tech_stack", "tech_stack", postgresql_using="gin"),
        # Partial indexes: essentially every UI query carries status='open',
        # and these stay small as closed listings accumulate.
        Index(
            "ix_jobs_open_posted",
            "posted_at",
            "id",
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_jobs_dedup_key", "dedup_key"),
        Index("ix_jobs_content_hash", "content_hash"),
        Index("ix_jobs_canonical_job_id", "canonical_job_id"),
    )


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.running.value)
    #: How the run was started: "cli", "schedule", "test".
    trigger: Mapped[str] = mapped_column(String(50), default="cli")

    #: Anthropic batch id, stored so a crashed process resumes by re-polling
    #: rather than re-submitting and paying twice.
    batch_id: Mapped[str | None] = mapped_column(String(100))

    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    stats: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")

    sources: Mapped[list[ScrapeRunSource]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (_check("status", RunStatus, "status"),)


class ScrapeRunSource(Base):
    """Per-source outcome of one run. This is what makes failures visible."""

    __tablename__ = "scrape_run_sources"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("scrape_runs.id", ondelete="CASCADE"))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))

    status: Mapped[str] = mapped_column(String(20), default=SourceStatus.pending.value)
    jobs_found: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    jobs_new: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    jobs_updated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    jobs_closed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    http_requests: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    http_status: Mapped[int | None] = mapped_column(Integer)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")

    error_class: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[ScrapeRun] = relationship(back_populates="sources")

    __table_args__ = (
        UniqueConstraint("run_id", "source_id", name="uq_run_source"),
        _check("status", SourceStatus, "status"),
        Index("ix_run_sources_source_id", "source_id"),
    )


class LlmCall(Base):
    """One row per Anthropic request. Cost is found at $6, not at $600."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("scrape_runs.id", ondelete="CASCADE"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    #: "extract_html" | "enrich" | "detect_source"
    purpose: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[int] = mapped_column(SmallInteger)
    batched: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="ok")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (Index("ix_llm_calls_run_id", "run_id"),)


class RawPage(Base):
    """Fetched HTML, kept so re-extraction never refetches.

    Content-addressed: the body lives on disk under
    ``settings.page_cache_dir`` sharded by hash prefix, and this row holds the
    metadata. Disk rather than ``bytea`` keeps the database small enough to
    dump casually; the store is regenerable, so leaving it out of backups is
    an acceptable trade.
    """

    __tablename__ = "raw_pages"

    body_sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(200))
    etag: Mapped[str | None] = mapped_column(String(300))
    last_modified: Mapped[str | None] = mapped_column(String(100))
    #: True when the body came from Playwright rather than plain httpx.
    rendered: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    byte_len: Mapped[int | None] = mapped_column(Integer)
    body_path: Mapped[str] = mapped_column(Text)

    __table_args__ = (Index("ix_raw_pages_url", "url"),)
