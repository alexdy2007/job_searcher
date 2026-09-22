"""Adapter contract.

Adapters never touch the database. They turn a `Source` row plus an HTTP
response into `RawListing` objects; the pipeline owns persistence. That split
is what lets adapters be tested offline against recorded fixtures with no
Postgres, and the persistence layer be tested with no network.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import datetime
from typing import ClassVar

import httpx
from pydantic import BaseModel, Field

from job_searcher.db.models import ExtractionMethod, SourceKind


class RawListing(BaseModel):
    """One job as an adapter sees it -- deterministic fields only.

    Nothing here is inferred. Enrichment fields (salary, seniority, remote
    policy, tech stack) are absent by design: an adapter that guessed them
    would put model output in a column the UI presents as fact.
    """

    external_id: str
    url: str
    title: str
    company: str
    location_raw: str | None = None
    department: str | None = None
    team: str | None = None
    employment_type: str | None = None
    description_html: str | None = None
    description_text: str | None = None
    posted_at: datetime | None = None
    #: 'second' | 'day' | 'unknown'. Many feeds give a date only, and without
    #: this the UI would render "posted 3 hours ago" from a bare date.
    posted_at_precision: str | None = None
    apply_url: str | None = None
    extraction_method: ExtractionMethod = ExtractionMethod.ats
    raw: dict = Field(default_factory=dict)


class AdapterError(RuntimeError):
    """The response could not be parsed as this ATS's format."""


class SourceAdapter(ABC):
    kind: ClassVar[SourceKind]

    #: Requests per minute this ATS tolerates for one board.
    default_rate_limit: ClassVar[int] = 30

    @abstractmethod
    def index_url(self, board_token: str) -> str:
        """The single endpoint listing every job on the board."""

    @abstractmethod
    def parse(self, payload: object, *, company_name: str) -> Iterable[RawListing]:
        """Turn a decoded response into listings."""

    def build_request(self, board_token: str, *, user_agent: str) -> httpx.Request:
        return httpx.Request(
            "GET",
            self.index_url(board_token),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )


_REGISTRY: dict[SourceKind, type[SourceAdapter]] = {}


def register(cls: type[SourceAdapter]) -> type[SourceAdapter]:
    _REGISTRY[cls.kind] = cls
    return cls


def get_adapter(kind: SourceKind | str) -> SourceAdapter:
    key = SourceKind(kind)
    try:
        return _REGISTRY[key]()
    except KeyError:
        known = ", ".join(sorted(k.value for k in _REGISTRY))
        raise AdapterError(
            f"No adapter for source kind {key.value!r}. Available: {known or '(none)'}"
        ) from None


def registered_kinds() -> list[str]:
    return sorted(k.value for k in _REGISTRY)
