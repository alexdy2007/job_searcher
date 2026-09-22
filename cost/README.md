# Subagent token telemetry

One CSV per subagent type, appended by the `SubagentStop` hook in
`.claude/settings.json`, which runs [`scripts/log_subagent_cost.py`](../scripts/log_subagent_cost.py).
One row per completed subagent run.

The CSVs are gitignored: they are per-machine and grow without bound. This
README is tracked so the format is documented.

## Columns

| Column | Meaning |
|---|---|
| `timestamp_utc` | When the subagent finished |
| `agent_type` | Which subagent (also the filename) |
| `agent_id`, `session_id` | For tracing back to a transcript |
| `model` | Dated model id as recorded, e.g. `claude-haiku-4-5-20251001` |
| `api_calls` | Distinct API responses, deduped by message id |
| `input_tokens`, `output_tokens` | Fresh (uncached) tokens |
| `cache_read_tokens`, `cache_creation_tokens` | Cache hits and writes; both billed |
| `thinking_tokens` | Subset of output spent on reasoning |
| `billable_tokens` | Sum of the four billed token classes |
| `cost_usd` | Priced from `config/model_policy.toml`; empty when the model has no entry |

`billable_tokens` includes cache reads and writes deliberately. A "total" of
just input plus output understates a cached agent by roughly an order of
magnitude — most of a subagent's tokens are its cached system prompt.

`cost_usd` is left **empty**, not `0.00`, for a model the policy does not
price. A zero would read as "this run was free".

## Two things worth knowing

**Dedup is what makes the numbers real.** One API response can occupy several
transcript lines — separate content blocks sharing a `message.id`, each
repeating the same aggregate usage block. Summing every assistant line
roughly doubles every figure. The script dedups by `message.id`; verified
against the harness's own reported `subagent_tokens`, which matches exactly.

**This covers subagents only.** Work done on the main thread has no
`SubagentStop` event and is not recorded here. To make a task show up in
these numbers, delegate it — which is also what puts it on its policy tier
rather than the default model.

## Reading the data

```powershell
uv run python -c "import pandas as pd, glob; df=pd.concat(map(pd.read_csv, glob.glob('cost/*.csv'))); print(df.groupby('agent_type')[['billable_tokens','cost_usd']].sum())"
```
