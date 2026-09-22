# job-searcher

[![CI](https://github.com/alexdy2007/job_searcher/actions/workflows/ci.yml/badge.svg)](https://github.com/alexdy2007/job_searcher/actions/workflows/ci.yml)

Collects job listings from ATS boards (Greenhouse, Lever, Ashby, Workable) and
company career pages, normalizes them into Postgres, and serves them through a
Streamlit UI for search and filtering.

Listings are stored with `first_seen_at` / `last_seen_at`, so the history that
career sites discard is preserved locally.

## Quick start

```powershell
uv sync
Copy-Item .env.example .env      # then fill in ANTHROPIC_API_KEY
docker compose up -d db
uv run alembic upgrade head
uv run job-searcher scrape
uv run streamlit run app/Home.py
```

## How extraction works

Hybrid, by design:

- **ATS boards** expose public JSON. A deterministic adapter reads it. No LLM,
  no cost, no drift.
- **Company career pages** are HTML. They are reduced to text with
  `trafilatura`, then parsed by Claude into a Pydantic schema.
- **Free-text fields** that no source provides as data -- salary range,
  seniority, remote policy, tech stack -- are enriched by Claude through the
  Batch API.

Enrichment is gated on `content_hash`, so re-scraping unchanged listings costs
nothing.

## Adding a company

One row in `sources`, no code:

```powershell
uv run job-searcher sources add --kind greenhouse --company "Acme" --token acmeinc
```

See `.claude/skills/add-job-source/` for the full onboarding procedure.
