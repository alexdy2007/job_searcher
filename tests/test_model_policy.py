"""Tests for the model routing policy, the router, and cost accounting.

No network and no API key: everything here is pure logic over the policy file.
"""

from __future__ import annotations

import pytest

from job_searcher.llm.policy import (
    VALID_CC_ALIASES,
    ModelPolicy,
    PolicyError,
    load_policy,
)
from job_searcher.llm.pricing import BudgetExceeded, CostLedger, Usage, cost_usd
from job_searcher.llm.router import ModelRouter


@pytest.fixture(scope="module")
def policy() -> ModelPolicy:
    return load_policy()


@pytest.fixture
def router(policy: ModelPolicy) -> ModelRouter:
    return ModelRouter(policy=policy, ledger=CostLedger(budget_usd=0.0))


# --- the policy file itself ------------------------------------------------
def test_real_policy_file_loads(policy: ModelPolicy):
    assert policy.tiers and policy.runtime_tasks and policy.subagents


def test_every_tier_is_priced(policy: ModelPolicy):
    """An unpriced model reports $0, which is how an unnoticed model change
    becomes an unnoticed bill."""
    for tier in policy.tiers.values():
        assert tier.api_model in policy.pricing


def test_tier_aliases_are_valid_for_the_agent_tool(policy: ModelPolicy):
    for tier in policy.tiers.values():
        assert tier.claude_code_alias in VALID_CC_ALIASES


def test_no_task_sends_effort_to_a_tier_that_rejects_it(policy: ModelPolicy):
    """Haiku 4.5 returns a 400 for output_config.effort. That would only ever
    break the cheap path -- the one added to save money, and so the one
    carrying the most traffic."""
    for task in policy.runtime_tasks.values():
        if task.effort is not None:
            assert task.tier.supports_effort, (
                f"task {task.name!r} sets effort on tier {task.tier.name!r}, "
                f"which does not accept it"
            )


# --- validation catches bad policies --------------------------------------
_MINIMAL = """
version = 1
[tiers.cheap]
api_model = "claude-haiku-4-5"
claude_code_alias = "haiku"
supports_effort = false
supports_adaptive_thinking = false
context_window = 200000
rationale = "x"
[runtime_tasks.t]
tier = "cheap"
batch = false
max_tokens = 1024
{extra}
[pricing.claude-haiku-4-5]
input = 1.0
output = 5.0
[multipliers]
cache_read = 0.1
cache_write = 1.25
batch = 0.5
"""


def _write(tmp_path, extra: str = ""):
    p = tmp_path / "policy.toml"
    p.write_text(_MINIMAL.format(extra=extra), encoding="utf-8")
    return p


def test_effort_on_unsupported_tier_is_rejected(tmp_path):
    with pytest.raises(PolicyError, match="does not support"):
        load_policy(_write(tmp_path, 'effort = "low"'))


def test_valid_minimal_policy_loads(tmp_path):
    assert load_policy(_write(tmp_path)).runtime_tasks["t"].effort is None


def test_unknown_task_names_the_known_ones(policy: ModelPolicy):
    with pytest.raises(PolicyError, match="Known tasks"):
        policy.task("no_such_task")


# --- routing ---------------------------------------------------------------
def test_request_kwargs_omits_effort_for_cheap_tier(router: ModelRouter):
    kwargs = router.request_kwargs("enrich_description")
    assert kwargs["model"] == "claude-haiku-4-5"
    assert "output_config" not in kwargs


def test_request_kwargs_includes_effort_where_supported(router: ModelRouter):
    kwargs = router.request_kwargs("extract_html")
    assert kwargs["output_config"] == {"effort": "low"}


def test_bulk_enrichment_is_batched(router: ModelRouter):
    """Enrichment is never latency-sensitive, and the Batch API halves it."""
    assert router.is_batched("enrich_description")


# --- cost ------------------------------------------------------------------
def test_cost_matches_published_rates(policy: ModelPolicy):
    # 1M input + 1M output on Opus 5 at $5/$25.
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd(usage, "claude-opus-5", policy=policy) == pytest.approx(30.0)


def test_batch_halves_cost(policy: ModelPolicy):
    usage = Usage(input_tokens=1_000_000)
    full = cost_usd(usage, "claude-haiku-4-5", policy=policy)
    batched = cost_usd(usage, "claude-haiku-4-5", batched=True, policy=policy)
    assert batched == pytest.approx(full * 0.5)


def test_cache_reads_are_cheaper_than_fresh_input(policy: ModelPolicy):
    fresh = cost_usd(Usage(input_tokens=1_000_000), "claude-opus-5", policy=policy)
    cached = cost_usd(
        Usage(cache_read_tokens=1_000_000), "claude-opus-5", policy=policy
    )
    assert cached == pytest.approx(fresh * 0.1)


def test_unpriced_model_raises_rather_than_reporting_zero(policy: ModelPolicy):
    with pytest.raises(PolicyError, match="No pricing"):
        cost_usd(Usage(input_tokens=1000), "claude-not-a-model", policy=policy)


def test_budget_stops_the_run(policy: ModelPolicy):
    ledger = CostLedger(budget_usd=0.01)
    with pytest.raises(BudgetExceeded, match="ceiling"):
        for _ in range(10):
            ledger.record(
                Usage(input_tokens=500_000),
                "claude-opus-5",
                task="extract_html",
                policy=policy,
            )


def test_zero_budget_disables_the_ceiling(policy: ModelPolicy):
    ledger = CostLedger(budget_usd=0.0)
    ledger.record(
        Usage(input_tokens=10_000_000), "claude-opus-5", task="t", policy=policy
    )
    assert ledger.total_usd > 0


def test_overspending_call_is_still_recorded(policy: ModelPolicy):
    """The ledger must reflect what was actually spent, not what fit."""
    ledger = CostLedger(budget_usd=0.001)
    with pytest.raises(BudgetExceeded):
        ledger.record(
            Usage(input_tokens=1_000_000), "claude-opus-5", task="t", policy=policy
        )
    assert ledger.total_usd == pytest.approx(5.0)
    assert ledger.calls == 1


def test_compare_tiers_orders_cheap_below_premium(router: ModelRouter):
    costs = router.compare_tiers(count=1000, input_tokens=4000, output_tokens=500)
    assert costs["cheap"] < costs["standard"] < costs["premium"]
