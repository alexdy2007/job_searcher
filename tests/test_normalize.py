"""Tests for the deterministic normalisation rules.

These decide job identity. A change to `content_hash` silently re-enriches the
whole database; a change to `dedup_key` silently re-groups it. So the rules are
pinned here rather than left to be rediscovered.
"""

from __future__ import annotations

import pytest

from job_searcher.normalize import (
    canonical_url,
    content_hash,
    dedup_key,
    html_to_text,
    location_bucket,
    normalize_title,
    normalize_ws,
    slugify,
)


# --- URL canonicalisation --------------------------------------------------
def test_tracking_parameters_are_stripped():
    url = "https://boards.greenhouse.io/figma/jobs/123?utm_source=x&gh_src=abc&ref=y"
    assert canonical_url(url) == "https://boards.greenhouse.io/figma/jobs/123"


def test_gh_jid_is_kept():
    """On many boards the posting URL is a generic search page and gh_jid is
    the only thing identifying the job. Stripping it would collapse every one
    of that company's jobs onto a single URL."""
    url = "https://stripe.com/jobs/search?gh_jid=8172510&gh_src=tracking"
    assert canonical_url(url) == "https://stripe.com/jobs/search?gh_jid=8172510"


def test_fragment_and_trailing_slash_are_dropped():
    assert canonical_url("https://x.com/jobs/1/#apply") == "https://x.com/jobs/1"


def test_empty_url_is_empty():
    assert canonical_url("") == ""


# --- HTML ------------------------------------------------------------------
def test_entity_escaped_html_is_unescaped_before_stripping():
    """Greenhouse returns `content` entity-escaped: the field literally holds
    '&lt;p&gt;'. Without unescaping first, tag stripping is a no-op and the
    markup survives as visible text."""
    escaped = "&lt;div&gt;&lt;p&gt;Build &amp; ship&lt;/p&gt;&lt;/div&gt;"
    text = html_to_text(escaped)
    assert text == "Build & ship"
    assert "<" not in text and "&lt;" not in text


def test_plain_html_also_works():
    assert html_to_text("<p>Hello <b>world</b></p>") == "Hello world"


def test_empty_description_is_empty_string():
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


# --- whitespace ------------------------------------------------------------
def test_crlf_and_lf_hash_identically():
    """A page captured on Windows and the same page captured on Linux must
    hash the same, or every listing looks changed depending on who scraped it."""
    assert normalize_ws("a\r\nb") == normalize_ws("a\nb") == "a b"


def test_whitespace_runs_collapse():
    assert normalize_ws("  a   \n\n  b  ") == "a b"


# --- titles ----------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Senior Engineer (Remote)", "senior engineer"),
        ("Senior Engineer [Hybrid]", "senior engineer"),
        ("Engineer II", "engineer 2"),
        ("  Staff   Engineer  ", "staff engineer"),
    ],
)
def test_title_normalisation(raw, expected):
    assert normalize_title(raw) == expected


def test_roman_numeral_and_digit_titles_agree():
    assert normalize_title("Engineer III") == normalize_title("Engineer 3")


# --- location bucketing ----------------------------------------------------
def test_remote_roles_in_different_countries_do_not_merge():
    """The real failure this guards: bucketing every remote role to a bare
    "remote" merged 11 distinct GitLab postings. Over-grouping hides a real
    job behind another; under-grouping only shows a duplicate."""
    assert location_bucket("Remote, Poland") != location_bucket("Remote, United Kingdom")


def test_multi_location_uses_the_first_place():
    assert location_bucket("San Francisco, CA • New York, NY") == location_bucket(
        "San Francisco, CA"
    )


def test_blank_location_is_empty():
    assert location_bucket(None) == ""


# --- hashes ----------------------------------------------------------------
def test_content_hash_is_stable_for_equal_content():
    a = content_hash("Engineer", "London", "Full-time", "Build things")
    b = content_hash("Engineer", "London", "Full-time", "Build things")
    assert a == b and len(a) == 64


def test_content_hash_changes_when_the_description_changes():
    a = content_hash("Engineer", "London", None, "Build things")
    b = content_hash("Engineer", "London", None, "Build other things")
    assert a != b


def test_content_hash_fields_cannot_bleed_into_each_other():
    """Without a separator, ("ab","c") and ("a","bc") would collide."""
    assert content_hash("ab", "c", None, None) != content_hash("a", "bc", None, None)


def test_dedup_key_groups_the_same_role_across_sources():
    a = dedup_key("figma", "Senior Engineer (Remote)", "London")
    b = dedup_key("figma", "Senior Engineer", "London")
    assert a == b


def test_dedup_key_separates_different_companies():
    assert dedup_key("figma", "Engineer", "London") != dedup_key(
        "gitlab", "Engineer", "London"
    )


def test_slugify_handles_accents_and_punctuation():
    assert slugify("Zürich & Co.") == "zurich-co"
