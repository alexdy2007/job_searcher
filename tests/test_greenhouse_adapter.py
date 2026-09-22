"""Greenhouse adapter contract tests.

Run entirely offline against a recorded payload. When Greenhouse renames a
field, these fail with a diff instead of the adapter quietly producing rows
full of nulls -- which is the failure mode that matters, because a board
returning 0 usable jobs looks exactly like a board where every job was filled.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from job_searcher.collect import get_adapter
from job_searcher.collect.base import AdapterError
from job_searcher.collect.fetch import FetchError, build_client, fetch_json

FIXTURE = Path(__file__).parent / "fixtures" / "http" / "greenhouse_figma.json"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture
def adapter():
    return get_adapter("greenhouse")


def test_index_url_shape(adapter):
    assert adapter.index_url("figma") == (
        "https://boards-api.greenhouse.io/v1/boards/figma/jobs?content=true"
    )


def test_every_fixture_job_parses(adapter, payload):
    rows = list(adapter.parse(payload, company_name="Figma"))
    assert len(rows) == len(payload["jobs"]) == 5


def test_required_fields_are_populated(adapter, payload):
    for row in adapter.parse(payload, company_name="Figma"):
        assert row.external_id and row.title and row.url and row.company


def test_external_ids_are_unique(adapter, payload):
    rows = list(adapter.parse(payload, company_name="Figma"))
    assert len({r.external_id for r in rows}) == len(rows)


def test_description_has_no_surviving_markup(adapter, payload):
    """The `content` field arrives entity-escaped; if it is not unescaped
    before stripping, the text keeps its tags as visible characters."""
    for row in adapter.parse(payload, company_name="Figma"):
        assert row.description_text
        assert "&lt;" not in row.description_text
        assert "&amp;" not in row.description_text
        assert "<div" not in row.description_text
        assert "<p>" not in row.description_text


def test_description_html_is_real_html(adapter, payload):
    row = next(iter(adapter.parse(payload, company_name="Figma")))
    assert "<" in row.description_html and "&lt;" not in row.description_html


def test_posted_at_is_timezone_aware(adapter, payload):
    for row in adapter.parse(payload, company_name="Figma"):
        assert row.posted_at is not None
        assert row.posted_at.tzinfo is not None
        assert row.posted_at_precision == "second"


def test_multi_location_string_is_preserved_raw(adapter, payload):
    """Splitting locations is the normaliser's job, not the adapter's -- the
    adapter records what the board said."""
    rows = list(adapter.parse(payload, company_name="Figma"))
    assert any("•" in (r.location_raw or "") for r in rows)


def test_company_falls_back_when_the_feed_omits_it(adapter):
    payload = {"jobs": [{"id": 1, "title": "Engineer", "absolute_url": "https://x/1"}]}
    row = next(iter(adapter.parse(payload, company_name="Fallback Co")))
    assert row.company == "Fallback Co"


def test_rows_missing_an_identifier_are_skipped_not_fatal(adapter):
    """A single malformed row must not abort a 200-job board."""
    payload = {
        "jobs": [
            {"id": 1, "title": "Good", "absolute_url": "https://x/1"},
            {"id": 2, "title": "", "absolute_url": "https://x/2"},  # no title
            {"title": "No id", "absolute_url": "https://x/3"},
            {"id": 4, "title": "No url"},
            "not even a dict",
        ]
    }
    rows = list(adapter.parse(payload, company_name="X"))
    assert [r.title for r in rows] == ["Good"]


def test_placeholder_department_is_dropped(adapter):
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Engineer",
                "absolute_url": "https://x/1",
                "departments": [{"id": 1, "name": "No Department"}],
            }
        ]
    }
    assert next(iter(adapter.parse(payload, company_name="X"))).department is None


def test_unexpected_payload_shape_raises_a_clear_error(adapter):
    """Schema drift must fail loudly. Returning zero rows instead would look
    identical to a board where every job closed."""
    with pytest.raises(AdapterError, match="no 'jobs' key"):
        list(adapter.parse({"results": []}, company_name="X"))
    with pytest.raises(AdapterError, match="not a list"):
        list(adapter.parse({"jobs": {}}, company_name="X"))


# --- fetch layer -----------------------------------------------------------
@respx.mock
def test_fetch_returns_parsed_json(adapter, payload):
    url = adapter.index_url("figma")
    respx.get(url).mock(return_value=httpx.Response(200, json=payload, headers={"etag": "W/x"}))
    with build_client() as client:
        result = fetch_json(client, adapter.build_request("figma", user_agent="test"))
    assert result.status == 200
    assert result.etag == "W/x"
    assert len(result.payload["jobs"]) == 5


@respx.mock
def test_304_is_reported_as_not_modified(adapter):
    """The cheapest outcome: nothing to parse, nothing to pay for."""
    respx.get(adapter.index_url("figma")).mock(return_value=httpx.Response(304))
    with build_client() as client:
        result = fetch_json(
            client, adapter.build_request("figma", user_agent="test"), etag="W/x"
        )
    assert result.not_modified and result.payload is None


@respx.mock
def test_404_raises_without_retrying(adapter):
    """A dead board token must surface immediately; retrying a 404 just
    hammers a board that will never answer."""
    route = respx.get(adapter.index_url("nope")).mock(return_value=httpx.Response(404))
    with build_client() as client, pytest.raises(FetchError) as exc:
        fetch_json(client, adapter.build_request("nope", user_agent="test"))
    assert exc.value.status == 404
    assert route.call_count == 1


@respx.mock
def test_non_json_response_raises_a_useful_error(adapter):
    respx.get(adapter.index_url("figma")).mock(
        return_value=httpx.Response(200, text="<html>maintenance</html>")
    )
    with build_client() as client, pytest.raises(FetchError, match="not JSON"):
        fetch_json(client, adapter.build_request("figma", user_agent="test"))


@respx.mock
def test_server_errors_are_retried(adapter, payload):
    route = respx.get(adapter.index_url("figma")).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json=payload),
        ]
    )
    with build_client() as client:
        result = fetch_json(client, adapter.build_request("figma", user_agent="test"))
    assert result.status == 200
    assert route.call_count == 2
