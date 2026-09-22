---
name: run-scrape
description: Run the job_searcher scrape pipeline locally and interpret the results. Use when asked to scrape, refresh listings, fetch new jobs, re-run a source, or diagnose what a run did. Covers flags, cost control, and reading the run report.
---

# Running a scrape

```powershell
docker compose up -d db
uv run job-searcher scrape
```

## Flags

| Flag | Effect |
|---|---|
| `--source <id>` | One source only. Use this while developing an adapter. |
| `--dry-run` | Parse and report, write nothing. |
| `--no-llm` | Skip extraction and enrichment entirely. Free, and the fastest way to check the fetch path. |
| `--from-cache` | Re-extract from cached HTML without refetching. Use for prompt iteration. |
| `--force-refresh` | Bypass the page cache and conditional GETs. Use sparingly — it re-fetches everything. |
| `--since 7d` | Limit to sources or listings touched in a window. |
| `--resume <run_id>` | Skip sources that already succeeded in that run. |

## What a run does

Phases run in order, and each is contained: `discover` → `fetch/parse/upsert`
per source → `enrich` (batched across all sources) → `sweep` (closure
detection) → `finalize`.

Enrichment runs **after** every source settles so that one Batch API
submission covers the whole run. That is where the 50% batch discount and the
prompt cache actually pay off.

A source that fails does not abort the run. Its exception is caught, recorded
on `scrape_run_sources`, and the run continues.

## Reading the result

```powershell
uv run job-searcher runs show --last
```

Statuses that matter:

- **`suspect_drift`** — the source fetched fine, but the result failed the
  sanity gate (zero jobs where there were many, a collapse against the recent
  median, or a high null rate on `title`/`url`). The closure sweep is
  **skipped** for that source. This is the guard that stops a broken adapter
  from marking an entire board as closed. Investigate with the
  `adapter-doctor` subagent; never clear the flag by hand.
- **`skipped_304`** — unchanged since last fetch. No parse, no LLM, no cost.
  Lots of these is the system working correctly.
- **`skipped_robots`** — `robots.txt` disallows the path. Leave it alone.

## Cost

Each run records tokens and dollars on `scrape_runs`, per-call detail in
`llm_calls`. `RUN_COST_BUDGET_USD` in `.env` aborts the enrich phase and marks
the run `budget_exceeded` rather than letting a runaway crawl bill silently.

The idempotency check is the important one: **run a scrape twice in a row.**
The second run should insert zero new rows, update `last_seen_at`, and spend
roughly nothing — that single observation validates dedupe, upsert
idempotency, and the `content_hash` cost gate at once.

## Scheduling

There is no built-in scheduler by design. Point Windows Task Scheduler at
`uv run job-searcher scrape` — one entry point, one log stream.
