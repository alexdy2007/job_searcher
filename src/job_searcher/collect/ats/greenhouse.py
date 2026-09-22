"""Greenhouse job board adapter.

One request returns every job on a board *with* its full description, which
makes Greenhouse the cheapest source in the system: no per-job fetch, no LLM,
no pagination.

Endpoint shape (verified against live boards):
    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
    -> {"jobs": [...], "meta": {"total": N}}
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

from job_searcher.collect.base import AdapterError, RawListing, SourceAdapter, register
from job_searcher.db.models import SourceKind
from job_searcher.normalize import canonical_url, html_to_text, unescape_html

BASE = "https://boards-api.greenhouse.io/v1/boards"


def _parse_ts(value: str | None) -> tuple[datetime | None, str | None]:
    """Greenhouse timestamps are ISO 8601 with a numeric offset."""
    if not value:
        return None, None
    try:
        return datetime.fromisoformat(value), "second"
    except ValueError:
        return None, "unknown"


def _first_name(items: object) -> str | None:
    """Greenhouse nests departments and offices as lists of {id, name}."""
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                name = (item.get("name") or "").strip()
                # Greenhouse uses "No Department" as its own placeholder.
                if name and name.lower() not in {"no department", "no office"}:
                    return name
    return None


@register
class GreenhouseAdapter(SourceAdapter):
    kind: ClassVar[SourceKind] = SourceKind.greenhouse
    default_rate_limit: ClassVar[int] = 60

    def index_url(self, board_token: str) -> str:
        return f"{BASE}/{board_token}/jobs?content=true"

    def parse(self, payload: object, *, company_name: str) -> Iterable[RawListing]:
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise AdapterError(
                "Greenhouse response has no 'jobs' key. The board token may be "
                "wrong, or the payload shape changed."
            )
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise AdapterError("Greenhouse 'jobs' is not a list")

        for job in jobs:
            if not isinstance(job, dict):
                continue
            listing = self._one(job, company_name)
            if listing is not None:
                yield listing

    def _one(self, job: dict, company_name: str) -> RawListing | None:
        external_id = job.get("id")
        title = (job.get("title") or "").strip()
        url = job.get("absolute_url") or ""
        # A listing without these three cannot be stored or linked to, and a
        # silently-skipped row is better than a NOT NULL violation aborting
        # the whole board.
        if external_id is None or not title or not url:
            return None

        # `content` arrives entity-escaped: the field holds "&lt;p&gt;", not
        # "<p>". Both helpers unescape before doing their work.
        content = job.get("content")
        posted_at, precision = _parse_ts(job.get("first_published"))
        location = job.get("location")
        location_raw = (
            (location.get("name") or "").strip() if isinstance(location, dict) else None
        )

        return RawListing(
            external_id=str(external_id),
            url=canonical_url(url),
            title=title,
            # The board states the company; the source row's name may be a
            # local label, so prefer the feed and fall back.
            company=(job.get("company_name") or company_name or "").strip()
            or company_name,
            location_raw=location_raw or None,
            department=_first_name(job.get("departments")),
            team=_first_name(job.get("offices")),
            description_html=unescape_html(content) or None,
            description_text=html_to_text(content) or None,
            posted_at=posted_at,
            posted_at_precision=precision,
            apply_url=canonical_url(url),
            raw={
                "requisition_id": job.get("requisition_id"),
                "internal_job_id": job.get("internal_job_id"),
                "updated_at": job.get("updated_at"),
                "departments": job.get("departments"),
                "offices": job.get("offices"),
            },
        )
