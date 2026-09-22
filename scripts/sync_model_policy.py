"""Keep .claude/agents/*.md frontmatter in step with the model policy.

Without this, `config/model_policy.toml` would be a description of what
someone once intended and the frontmatter would be the thing that actually
runs -- and the two would drift within a week. CI runs this in check mode, so
the policy stays canonical.

    uv run python scripts/sync_model_policy.py          # check, exit 1 on drift
    uv run python scripts/sync_model_policy.py --fix     # rewrite frontmatter
"""

from __future__ import annotations

import argparse
import sys

from job_searcher.config import PROJECT_ROOT
from job_searcher.llm.policy import load_policy

AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"
FENCE = "---"


def _split_frontmatter(text: str) -> tuple[list[str], str] | None:
    """Return (frontmatter lines, rest) or None if there is no frontmatter."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != FENCE:
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == FENCE:
            return lines[1:i], "\n".join(lines[i + 1 :])
    return None


def _get_model(front: list[str]) -> str | None:
    for line in front:
        if line.startswith("model:"):
            return line.split(":", 1)[1].strip()
    return None


def _set_model(front: list[str], alias: str) -> list[str]:
    out = [ln for ln in front if not ln.startswith("model:")]
    # Place it after `description:` so the agent's purpose stays the first
    # thing a reader sees.
    insert_at = len(out)
    for i, line in enumerate(out):
        if line.startswith("tools:"):
            insert_at = i
            break
    out.insert(insert_at, f"model: {alias}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fix", action="store_true", help="rewrite frontmatter in place")
    args = parser.parse_args(argv)

    policy = load_policy()
    problems: list[str] = []
    fixed: list[str] = []

    for name, sub in sorted(policy.subagents.items()):
        path = AGENTS_DIR / f"{name}.md"
        if not path.exists():
            problems.append(f"{name}: policy lists this subagent but {path} does not exist")
            continue

        text = path.read_text(encoding="utf-8")
        split = _split_frontmatter(text)
        if split is None:
            problems.append(f"{name}: no YAML frontmatter found in {path.name}")
            continue

        front, body = split
        want = sub.tier.claude_code_alias
        have = _get_model(front)

        if have == want:
            continue

        if args.fix:
            new_front = _set_model(front, want)
            path.write_text(
                f"{FENCE}\n" + "\n".join(new_front) + f"\n{FENCE}\n" + body,
                encoding="utf-8",
            )
            fixed.append(f"{name}: {have or '(unset)'} -> {want}")
        else:
            problems.append(
                f"{name}: frontmatter says model={have or '(unset)'}, "
                f"policy says {want} (tier {sub.tier.name})"
            )

    # An agent file with no policy entry runs on the default model, which is
    # the expensive one. That is a silent cost, so it is an error too.
    for path in sorted(AGENTS_DIR.glob("*.md")):
        if path.stem not in policy.subagents:
            problems.append(
                f"{path.stem}: agent file exists but has no entry in "
                "config/model_policy.toml, so it runs on the default model"
            )

    for line in fixed:
        print(f"fixed  {line}")
    for line in problems:
        print(f"DRIFT  {line}", file=sys.stderr)

    if problems:
        print(
            f"\n{len(problems)} problem(s). Run: "
            "uv run python scripts/sync_model_policy.py --fix",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(policy.subagents)} subagent(s) match the model policy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
