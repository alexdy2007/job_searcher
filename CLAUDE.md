# job_searcher

Aggregates job listings from ATS boards (Greenhouse, Lever, Ashby, Workable)
and company career pages into Postgres, browsable via Streamlit.

## Delegate by task

Route work to the narrowest agent that covers it. Each runs on the model its
tier in `config/model_policy.toml` assigns, so delegating is what makes the
cost policy take effect — doing a subagent's work inline silently runs it on
the default (most expensive) model.

| The task involves | Use | Runs on |
|---|---|---|
| Adding a company, finding a careers page or board token, classifying a site's ATS | `source-onboarder` | sonnet |
| A source that failed, returned zero jobs, or is flagged `suspect_drift` | `adapter-doctor` | sonnet |
| Any edit to `db/models.py`, or writing/applying an Alembic migration | `schema-migrator` | opus |
| Extraction prompt, Pydantic schema, enrichment quality, or model/effort choices | `extraction-tuner` | opus |
| Pages, filters, or queries in `app/` | `streamlit-builder` | sonnet |

The two opus tiers are deliberate: migrations are hard to reverse and can
destroy scraped history, and the extraction tuner is the thing every other
cost decision gets measured against. Everything else is sonnet.

Work that spans several of these — a feature touching schema, extraction and
UI — should be split and delegated per layer rather than done as one pass.

## Skills

`add-job-source`, `db-migrate`, `run-scrape`, `extraction-eval`. Invoke the
skill for the procedure; delegate to the agent for the work.

## Model policy

`config/model_policy.toml` is canonical for **both** the runtime model per
LLM task and the model per subagent. Never hardcode a model string: call
`ModelRouter.request_kwargs("<task>")`. A hardcoded model is invisible to the
cost ledger and to the eval.

`scripts/sync_model_policy.py` propagates subagent tiers into agent
frontmatter; CI fails on drift.

Changing a runtime tier is a quality decision. Justify it with
`uv run pytest --llm tests/eval/` before and after — a saving is not a reason
on its own.

## Commands

```powershell
docker compose up -d db
uv run alembic upgrade head
uv run pytest                       # --llm adds the paid eval tests
uv run ruff check .
uv run python scripts/sync_model_policy.py
```

Postgres is on host port **5433** locally (CI uses 5432 — service containers
skip the Compose port mapping).

## Invariants

- A listing's identity is `(source_id, external_id)`. Upserts target that pair.
- `content_hash` gates enrichment. Re-scraping unchanged listings must cost ~$0.
- **Never mark jobs closed to work around a broken adapter.** A broken adapter
  and a board where every role was filled look identical; the `suspect_drift`
  sanity gate exists to tell them apart.
- Native Postgres enums only for `job_status` and `source_kind`. Churn-prone
  vocabularies are `text` + CHECK, because `ALTER TYPE ADD VALUE` cannot run
  in a transaction.
- Recorded fixtures are hashed byte-for-byte. Never reformat one.
