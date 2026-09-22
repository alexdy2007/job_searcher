"""Load and validate the model policy.

The policy is canonical: both the runtime router and the Claude Code subagent
frontmatter derive from it. Validation is strict and happens at load time, so
a policy that would fail at the API boundary fails here instead -- on a laptop
in milliseconds, rather than mid-run against a paid endpoint.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from job_searcher.config import PROJECT_ROOT

POLICY_PATH = PROJECT_ROOT / "config" / "model_policy.toml"

#: Values the Claude Code Agent tool accepts in `model:` frontmatter.
VALID_CC_ALIASES = {"haiku", "sonnet", "opus", "fable"}

#: Values accepted by `output_config.effort`.
VALID_EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class PolicyError(ValueError):
    """The policy file is internally inconsistent."""


@dataclass(frozen=True)
class Tier:
    name: str
    api_model: str
    claude_code_alias: str
    supports_effort: bool
    supports_adaptive_thinking: bool
    context_window: int
    rationale: str


@dataclass(frozen=True)
class RuntimeTask:
    name: str
    tier: Tier
    batch: bool
    max_tokens: int
    rationale: str
    effort: str | None = None


@dataclass(frozen=True)
class SubagentPolicy:
    name: str
    tier: Tier
    rationale: str


@dataclass(frozen=True)
class Multipliers:
    cache_read: float
    cache_write: float
    batch: float


@dataclass(frozen=True)
class ModelPolicy:
    version: int
    tiers: dict[str, Tier]
    runtime_tasks: dict[str, RuntimeTask]
    subagents: dict[str, SubagentPolicy]
    pricing: dict[str, dict[str, float]]
    multipliers: Multipliers

    def task(self, name: str) -> RuntimeTask:
        try:
            return self.runtime_tasks[name]
        except KeyError:
            known = ", ".join(sorted(self.runtime_tasks))
            raise PolicyError(
                f"No runtime task {name!r} in the model policy. Known tasks: {known}. "
                "Add it to config/model_policy.toml rather than hardcoding a model."
            ) from None


def _build_tier(name: str, raw: dict) -> Tier:
    missing = {
        "api_model",
        "claude_code_alias",
        "supports_effort",
        "supports_adaptive_thinking",
        "context_window",
        "rationale",
    } - raw.keys()
    if missing:
        raise PolicyError(f"tier {name!r} is missing: {', '.join(sorted(missing))}")

    alias = raw["claude_code_alias"]
    if alias not in VALID_CC_ALIASES:
        raise PolicyError(
            f"tier {name!r} has claude_code_alias {alias!r}; "
            f"the Agent tool only accepts {sorted(VALID_CC_ALIASES)}"
        )
    return Tier(name=name, **raw)


def _build_runtime_task(name: str, raw: dict, tiers: dict[str, Tier]) -> RuntimeTask:
    tier_name = raw.get("tier")
    if tier_name not in tiers:
        raise PolicyError(
            f"runtime task {name!r} references unknown tier {tier_name!r}"
        )
    tier = tiers[tier_name]
    effort = raw.get("effort")

    # The check that matters. Sending output_config.effort to Haiku 4.5 is a
    # 400, and it would only surface on the cheap path -- the one added to
    # save money, and therefore the one carrying the most traffic.
    if effort is not None:
        if not tier.supports_effort:
            raise PolicyError(
                f"runtime task {name!r} sets effort={effort!r}, but tier "
                f"{tier.name!r} ({tier.api_model}) does not support "
                "output_config.effort and will return a 400. Remove the effort key."
            )
        if effort not in VALID_EFFORTS:
            raise PolicyError(
                f"runtime task {name!r} has effort={effort!r}; "
                f"valid values are {sorted(VALID_EFFORTS)}"
            )

    max_tokens = raw.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens <= 0:
        raise PolicyError(f"runtime task {name!r} needs a positive integer max_tokens")

    return RuntimeTask(
        name=name,
        tier=tier,
        effort=effort,
        batch=bool(raw.get("batch", False)),
        max_tokens=max_tokens,
        rationale=raw.get("rationale", ""),
    )


def load_policy(path: Path | None = None) -> ModelPolicy:
    path = path or POLICY_PATH
    if not path.exists():
        raise PolicyError(f"Model policy not found at {path}")

    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    tiers = {name: _build_tier(name, cfg) for name, cfg in raw.get("tiers", {}).items()}
    if not tiers:
        raise PolicyError("policy defines no tiers")

    pricing = raw.get("pricing", {})
    for tier in tiers.values():
        if tier.api_model not in pricing:
            raise PolicyError(
                f"tier {tier.name!r} uses {tier.api_model!r}, which has no entry "
                "in [pricing]. Cost accounting would silently report $0."
            )

    tasks = {
        name: _build_runtime_task(name, cfg, tiers)
        for name, cfg in raw.get("runtime_tasks", {}).items()
    }

    subagents = {}
    for name, cfg in raw.get("subagents", {}).items():
        tier_name = cfg.get("tier")
        if tier_name not in tiers:
            raise PolicyError(f"subagent {name!r} references unknown tier {tier_name!r}")
        subagents[name] = SubagentPolicy(
            name=name, tier=tiers[tier_name], rationale=cfg.get("rationale", "")
        )

    mult = raw.get("multipliers", {})
    for key in ("cache_read", "cache_write", "batch"):
        if key not in mult:
            raise PolicyError(f"[multipliers] is missing {key!r}")

    return ModelPolicy(
        version=raw.get("version", 0),
        tiers=tiers,
        runtime_tasks=tasks,
        subagents=subagents,
        pricing=pricing,
        multipliers=Multipliers(
            cache_read=mult["cache_read"],
            cache_write=mult["cache_write"],
            batch=mult["batch"],
        ),
    )


@lru_cache
def get_policy() -> ModelPolicy:
    return load_policy()
