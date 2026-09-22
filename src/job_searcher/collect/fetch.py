"""HTTP fetching.

Identifies the bot, retries only what is worth retrying, and never retries a
403 or 404 -- hammering a board that has blocked or removed you makes the
situation worse, not better.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from job_searcher.config import settings

#: Retried: transport failures, rate limiting, and server errors.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _worth_retrying(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return False


@dataclass
class FetchResult:
    status: int
    payload: object | None
    etag: str | None = None
    last_modified: str | None = None
    #: True when the server said "unchanged" -- no parse, no LLM, no cost.
    not_modified: bool = False


def build_client(timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.user_agent},
    )


@retry(
    retry=retry_if_exception(_worth_retrying),
    wait=wait_exponential_jitter(initial=1, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _get(client: httpx.Client, request: httpx.Request) -> httpx.Response:
    response = client.send(request)
    if response.status_code in _RETRY_STATUS:
        # Raised so tenacity sees it; a non-retryable status falls through.
        response.raise_for_status()
    return response


def fetch_json(
    client: httpx.Client,
    request: httpx.Request,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    """GET and decode JSON, using a conditional request when we have validators.

    A 304 is the cheapest possible outcome: the board has not changed, so
    there is nothing to parse and nothing to pay for.
    """
    if etag:
        request.headers["If-None-Match"] = etag
    if last_modified:
        request.headers["If-Modified-Since"] = last_modified

    try:
        response = _get(client, request)
    except httpx.HTTPStatusError as exc:
        raise FetchError(
            f"{exc.response.status_code} from {request.url}",
            status=exc.response.status_code,
        ) from exc
    except httpx.TransportError as exc:
        raise FetchError(f"{type(exc).__name__} reaching {request.url}") from exc

    if response.status_code == 304:
        return FetchResult(status=304, payload=None, not_modified=True)

    if response.status_code >= 400:
        # 403/404 land here deliberately: not retried, surfaced to the caller
        # so the source can be marked rather than silently retried forever.
        raise FetchError(
            f"{response.status_code} from {request.url}", status=response.status_code
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise FetchError(
            f"Response from {request.url} is not JSON "
            f"(content-type {response.headers.get('content-type')!r})"
        ) from exc

    return FetchResult(
        status=response.status_code,
        payload=payload,
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )
