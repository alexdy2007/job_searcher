"""Task -> model routing.

Call sites name a *task*, never a model:

    kwargs = router.request_kwargs("extract_html")
    response = client.messages.parse(**kwargs, messages=[...], output_format=Job)

That indirection is the whole point. A hardcoded model string somewhere in
the extraction path is invisible to cost accounting and to the eval, so
changing it is untracked and unmeasured. Routing through the policy means
every model choice is in one file, with a rationale, and shows up in the
ledger.
"""

from __future__ import annotations

from dataclasses import dataclass

from job_searcher.config import settings
from job_searcher.llm.policy import ModelPolicy, RuntimeTask, get_policy
from job_searcher.llm.pricing import CostLedger, Usage, cost_usd


@dataclass
class ModelRouter:
    policy: ModelPolicy
    ledger: CostLedger

    @classmethod
    def for_run(cls, budget_usd: float | None = None) -> ModelRouter:
        return cls(
            policy=get_policy(),
            ledger=CostLedger(
                budget_usd=(
                    settings.run_cost_budget_usd if budget_usd is None else budget_usd
                )
            ),
        )

    # -- selection --------------------------------------------------------
    def model_for(self, task: str) -> str:
        return self.policy.task(task).tier.api_model

    def is_batched(self, task: str) -> bool:
        return self.policy.task(task).batch

    def request_kwargs(self, task: str) -> dict:
        """Kwargs ready to splat into a Messages API call.

        `output_config.effort` is emitted only for tiers that accept it --
        Haiku 4.5 returns a 400 for it. Thinking is left unset on purpose:
        Opus 5 and Sonnet 5 both run adaptive thinking when the parameter is
        omitted, and Haiku should not think for mechanical extraction.
        """
        spec: RuntimeTask = self.policy.task(task)
        kwargs: dict = {"model": spec.tier.api_model, "max_tokens": spec.max_tokens}
        if spec.effort and spec.tier.supports_effort:
            kwargs["output_config"] = {"effort": spec.effort}
        return kwargs

    # -- accounting -------------------------------------------------------
    def record(self, task: str, usage: Usage) -> float:
        """Record one call's usage against the run ledger. Raises
        BudgetExceeded once the run passes its ceiling."""
        spec = self.policy.task(task)
        return self.ledger.record(
            usage,
            spec.tier.api_model,
            task=task,
            batched=spec.batch,
            policy=self.policy,
        )

    # -- planning ---------------------------------------------------------
    def estimate(
        self, task: str, *, count: int, input_tokens: int, output_tokens: int
    ) -> float:
        """What `count` calls of this task would cost.

        Use this before a large backfill. Finding out afterwards is the
        expensive way to learn the same number.
        """
        spec = self.policy.task(task)
        per_call = cost_usd(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens),
            spec.tier.api_model,
            batched=spec.batch,
            policy=self.policy,
        )
        return per_call * count

    def compare_tiers(
        self, *, count: int, input_tokens: int, output_tokens: int, batched: bool = False
    ) -> dict[str, float]:
        """Cost of the same workload on every tier.

        Answers "what would moving this to a cheaper tier actually save?" in
        dollars. It deliberately says nothing about quality -- that only comes
        from the eval, and a saving is not a reason on its own.
        """
        usage = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
        return {
            tier.name: cost_usd(
                usage, tier.api_model, batched=batched, policy=self.policy
            )
            * count
            for tier in self.policy.tiers.values()
        }
