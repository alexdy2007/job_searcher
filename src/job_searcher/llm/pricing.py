"""Turn token usage into dollars, and stop a run that overspends.

Every Anthropic response carries a usage block. Converting it to a number at
the call site, rather than at the end of a run, is what makes a cost problem
visible at $6 instead of $600.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from job_searcher.llm.policy import ModelPolicy, PolicyError, get_policy

_PER_MILLION = 1_000_000


class BudgetExceeded(RuntimeError):
    """A run hit its configured spend ceiling."""


@dataclass(frozen=True)
class Usage:
    """The subset of an Anthropic usage block that costs money."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @classmethod
    def from_response(cls, response) -> Usage:
        """Build from an SDK response. Cache fields are absent on responses
        that did not use caching, so every lookup tolerates None."""
        u = response.usage
        return cls(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )


def cost_usd(
    usage: Usage,
    model: str,
    *,
    batched: bool = False,
    policy: ModelPolicy | None = None,
) -> float:
    """Cost of one call.

    Raises rather than defaulting to zero for an unpriced model: a silent $0
    is how an unnoticed model change becomes an unnoticed bill.
    """
    policy = policy or get_policy()
    try:
        rates = policy.pricing[model]
    except KeyError:
        raise PolicyError(
            f"No pricing for model {model!r}. Add it to [pricing] in "
            "config/model_policy.toml; otherwise its cost reports as $0."
        ) from None

    m = policy.multipliers
    total = (
        usage.input_tokens * rates["input"]
        + usage.output_tokens * rates["output"]
        + usage.cache_read_tokens * rates["input"] * m.cache_read
        + usage.cache_write_tokens * rates["input"] * m.cache_write
    ) / _PER_MILLION

    if batched:
        total *= m.batch
    return total


@dataclass
class CostLedger:
    """Accumulates spend for one run and enforces the ceiling.

    The ceiling is checked *after* recording, so the overspending call is
    still accounted for -- you want the ledger to reflect what was actually
    spent, not what was spent up to the last call that fit.
    """

    budget_usd: float = 0.0
    total_usd: float = 0.0
    calls: int = 0
    by_model: dict[str, float] = field(default_factory=dict)
    by_task: dict[str, float] = field(default_factory=dict)

    def record(
        self,
        usage: Usage,
        model: str,
        *,
        task: str,
        batched: bool = False,
        policy: ModelPolicy | None = None,
    ) -> float:
        amount = cost_usd(usage, model, batched=batched, policy=policy)
        self.total_usd += amount
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0.0) + amount
        self.by_task[task] = self.by_task.get(task, 0.0) + amount

        # budget_usd of 0 disables the ceiling, matching RUN_COST_BUDGET_USD.
        if self.budget_usd and self.total_usd > self.budget_usd:
            raise BudgetExceeded(
                f"Run spent ${self.total_usd:.4f}, over the "
                f"${self.budget_usd:.2f} ceiling, after {self.calls} calls. "
                f"By task: {self.format_by_task()}"
            )
        return amount

    def format_by_task(self) -> str:
        if not self.by_task:
            return "(nothing spent)"
        return ", ".join(
            f"{task}=${amount:.4f}"
            for task, amount in sorted(self.by_task.items(), key=lambda kv: -kv[1])
        )
