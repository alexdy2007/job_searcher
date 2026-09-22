"""Deterministic normalisation shared by every source adapter.

Nothing here calls an LLM. These are the rules that decide job identity, so
they must be stable: a change to `content_hash` or `dedup_key` silently
re-enriches or re-groups the whole database.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser

#: Query parameters that identify a *campaign*, not a job. Stripped from the
#: canonical URL so the same posting shared through two channels collapses to
#: one row.
_TRACKING_PREFIXES = ("utm_",)
_TRACKING_EXACT = {
    "gh_src",  # Greenhouse source tag; gh_jid is NOT tracking -- see below.
    "ref",
    "source",
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
}

#: Suffixes that describe the arrangement rather than the role, e.g.
#: "Engineer (Remote)". Removed for title comparison only -- never from the
#: stored title.
_TITLE_NOISE = re.compile(
    r"\s*[\(\[]\s*(remote|hybrid|onsite|on-site|contract|full[- ]time|part[- ]time)"
    r"[^)\]]*[\)\]]",
    re.IGNORECASE,
)

_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5"}


def canonical_url(url: str) -> str:
    """Strip campaign parameters and fragments, keeping identifying ones.

    ``gh_jid`` is deliberately **kept**. On many Greenhouse boards the posting
    URL is a generic search page and ``gh_jid`` is the only thing identifying
    the job (``stripe.com/jobs/search?gh_jid=8172510``), so treating it as
    tracking would collapse every one of that company's jobs onto one URL.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_EXACT
        and not any(k.lower().startswith(p) for p in _TRACKING_PREFIXES)
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, urlencode(kept), ""))


def html_to_text(raw: str | None) -> str:
    """Convert a description to plain text.

    Greenhouse returns ``content`` **entity-escaped** -- the field literally
    contains ``&lt;p&gt;`` rather than ``<p>``. Without unescaping first, tag
    stripping is a no-op and the "text" keeps its markup as visible
    characters. Unescaping twice is harmless for already-clean HTML, so this
    is safe for other sources too.
    """
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    text = LexborHTMLParser(unescaped).text(separator="\n")
    return normalize_ws(text)


def unescape_html(raw: str | None) -> str:
    """The description as real HTML, for storage and later re-extraction."""
    return html.unescape(raw) if raw else ""


def normalize_ws(text: str | None) -> str:
    """Collapse whitespace to a stable form.

    CRLF is folded to LF **before** anything else: a page captured on Windows
    and the same page captured on Linux must hash identically, or every
    listing looks changed depending on who scraped it.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return " ".join(text.split())


def slugify(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def normalize_title(title: str | None) -> str:
    """Title reduced for comparison: arrangement suffixes dropped, trailing
    roman numerals turned into digits so "Engineer II" and "Engineer 2" group
    together."""
    if not title:
        return ""
    cleaned = _TITLE_NOISE.sub("", title)
    cleaned = normalize_ws(cleaned).lower()
    words = cleaned.split()
    if words and words[-1] in _ROMAN:
        words[-1] = _ROMAN[words[-1]]
    return " ".join(words)


def location_bucket(location_raw: str | None) -> str:
    """Coarse location token for grouping.

    Deliberately keeps the qualifier on a remote role: "Remote, Poland" and
    "Remote, United Kingdom" are different jobs with different eligibility, so
    bucketing both to a bare "remote" merges postings that are not the same.
    Under-grouping merely shows a duplicate, which is visible and harmless;
    over-grouping hides a real job behind another, which is not.

    Whether a role *is* remote is `remote_policy`, an enrichment field. This
    key should not try to do that job.
    """
    text = normalize_ws(location_raw).lower()
    if not text:
        return ""
    # Multi-location strings separate places with bullets, semicolons or pipes.
    first = re.split(r"[•;|]|\s+/\s+", text)[0]
    return slugify(first)


def content_hash(
    title: str | None,
    location_raw: str | None,
    employment_type: str | None,
    description_text: str | None,
) -> str:
    """Identity of a listing's *content*. Changing => re-enrich.

    Excludes everything volatile: no timestamps, no ids, no view counters. A
    field separator that cannot appear in the inputs prevents
    ``("ab", "c")`` and ``("a", "bc")`` colliding.
    """
    parts = [
        normalize_ws(title),
        normalize_ws(location_raw),
        normalize_ws(employment_type),
        normalize_ws(description_text),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def dedup_key(company_slug: str, title: str | None, location_raw: str | None) -> str:
    """Groups the same role seen through more than one source.

    Never unique in the schema, and the loser of a group is linked rather than
    deleted, because this heuristic will sometimes be wrong -- two genuinely
    distinct "Senior Backend Engineer, London" requisitions do collide.
    """
    parts = [company_slug, normalize_title(title), location_bucket(location_raw)]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
