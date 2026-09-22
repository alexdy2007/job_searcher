---
name: add-job-source
description: Onboard a new company as a job source in job_searcher. Use when asked to add a company, add a careers page, track a new employer, or scrape a new site. Covers ATS detection, board token extraction, robots checks, fixture recording, and verification.
---

# Adding a job source

The goal of every onboarding is to land the company on the **ATS path**. An
ATS source is free, exact and stable; an HTML source costs an LLM call per
page and breaks when the layout changes. Most career pages that look custom
are an ATS in a wrapper, so always check before falling back.

## 1. Detect the ATS

```powershell
uv run job-searcher sources detect --url https://example.com/careers
```

This fetches the page and looks for board tokens in links, embed `<script>`
tags, and `__NEXT_DATA__` blobs. It prints a ready-to-use `sources add`
command, or reports that no ATS was found.

The token is often only in an embed script, never in a visible link — so
trust the detector over reading the page yourself.

## 2. Probe the token before trusting it

```powershell
uv run job-searcher sources probe --kind greenhouse --token acmeinc
```

A token that 404s is worse than no source at all: it fails quietly on every
run. Do not proceed until this returns a plausible job count.

## 3. Add the source

```powershell
uv run job-searcher sources add --kind greenhouse --company "Acme" --token acmeinc --url https://acme.com/careers
```

For a page with no ATS behind it:

```powershell
uv run job-searcher sources add --kind html --company "Acme" --url https://acme.com/careers
```

HTML sources are checked against `robots.txt` before the first fetch. If the
path is disallowed, the source is added but marked with `blocked_reason` and
skipped. Do not work around that.

## 4. Record a fixture

```powershell
uv run job-searcher dev record --source <id>
```

This writes the real response into `tests/fixtures/http/`, giving the adapter
an offline regression test that fails loudly when the ATS changes its payload
shape. Fixtures are hashed byte-for-byte — never reformat or prettify one,
and note that `.gitattributes` deliberately excludes them from line-ending
normalization.

## 5. Verify

```powershell
uv run job-searcher scrape --source <id> --dry-run
uv run job-searcher scrape --source <id>
```

Compare the count against what the careers page itself claims. A shortfall
usually means pagination is being missed, not that the board is small.

## Delegating

For anything beyond a single straightforward company — a batch of companies,
or a page where detection fails — hand it to the `source-onboarder` subagent,
which is built for this and will do the HTML spelunking for board tokens.
