---
name: extraction-eval
description: Measure LLM extraction and enrichment quality in job_searcher against the golden set. Use before and after any change to the extraction prompt, the Pydantic schema, the effort level, or the model. Reports per-field accuracy and cost per listing.
---

# Evaluating extraction

Extraction quality cannot be eyeballed, and it is not deterministic — you
cannot pin sampling, because `temperature` and `top_p` are removed on current
models and sending them returns a 400. So the only way to know whether a
prompt change helped is to measure it.

**This eval is the gate. No prompt, schema, effort, or model change lands
without a before-and-after run.**

## Run it

```powershell
uv run pytest --llm tests/eval/ -q
```

This hits the real API and costs money, which is why it is behind `--llm` and
excluded from the default `pytest` run.

## What it reports

Per-field scoring across ~20-30 hand-labelled real pages:

| Field | Metric |
|---|---|
| `seniority`, `remote_policy`, `employment_type` | exact match |
| `salary_min` / `salary_max` | recall when present, plus a tolerance band |
| `salary_currency` | exact |
| `tech_stack` | set F1 |
| `location_*` | exact on country, fuzzy on city |

Plus tokens and dollars per listing, and a cache-hit assertion.

## Reading the output

- **A single headline number hides regressions.** A change that lifts salary
  recall while quietly destroying seniority accuracy is a net loss. Read the
  per-field table, not the average.
- **`cache_read_input_tokens` must be > 0 on the second call.** A zero means
  something volatile got into the cached system prefix and you are paying
  full price on every request. Fix that before judging anything else.
- **Cost per listing is part of the result.** A prompt that improves accuracy
  by one point and triples cost is usually not worth it.

## Keeping the golden set honest

It must span the cases that actually break things: an ATS page, a messy
custom HTML page, a JS-rendered page, a page with no salary, a page with
multiple locations, and a page listing several roles at once. A golden set of
only clean pages measures nothing useful.

Labels are hand-written and are the ground truth. When the model disagrees
with a label, check the label first — sometimes the model is right and the
label is wrong, and silently "fixing" the eval to match the model destroys
its value.

## Iterating cheaply

Re-extract from the content-addressed HTML cache rather than refetching:

```powershell
uv run job-searcher extract --from-cache --since 7d
```

Bump `EXTRACTION_VERSION` when the prompt changes so the `content_hash` gate
re-runs the affected listings.

## Deciding on a model change

If a cheaper model is being considered for bulk enrichment, it is a decision
for the user, not an assumption to make — and it must be argued from this
eval's table, not from intuition. Exhaust the free levers first: HTML
reduction, the `content_hash` gate, prompt caching, and the Batch API. Those
cost no quality at all, and together they are worth far more than a model
swap.
