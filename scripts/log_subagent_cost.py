"""SubagentStop hook: append one row of token usage per subagent run.

Reads the hook payload on stdin and appends to ``cost/<agent_type>.csv``.

Two things this has to get right, both discovered by inspecting a real
payload rather than assumed:

1. **The hook payload contains no token usage.** It carries ``agent_type``
   and ``agent_transcript_path``; the tokens live in that transcript.

2. **One API response can span several transcript lines.** Entries share a
   ``message.id`` while having distinct ``uuid``s (they are separate content
   blocks of one response, numbered by ``apiBlockIndex``), and each line
   repeats the *same* aggregate usage block. Summing every assistant line
   therefore double-counts. Dedup by ``message.id`` is what makes the numbers
   real.

A hook that throws is worse than no hook -- it interrupts the session for
telemetry. Every failure path here exits 0.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

#: Matches a trailing model date stamp ("-20251001") and/or a context-window
#: tag ("[1m]"), so a dated id resolves to its pricing-table key.
_MODEL_SUFFIX = re.compile(r"(-\d{8})?(\[\w+\])?$")

COLUMNS = [
    "timestamp_utc",
    "agent_type",
    "agent_id",
    "session_id",
    "model",
    "api_calls",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "thinking_tokens",
    "billable_tokens",
    "cost_usd",
]


def normalize_model(model: str) -> str:
    return _MODEL_SUFFIX.sub("", model or "")


def read_transcript(path: Path) -> tuple[dict[str, int], str, int]:
    """Sum usage across a subagent transcript, deduped by message id."""
    totals = dict.fromkeys(
        ["input", "output", "cache_read", "cache_creation", "thinking"], 0
    )
    seen: set[str] = set()
    model = ""
    calls = 0

    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue  # a partially flushed final line is not worth failing over
            if entry.get("type") != "assistant":
                continue

            msg = entry.get("message") or {}
            msg_id = msg.get("id")
            # The dedup that keeps the numbers honest.
            if msg_id:
                if msg_id in seen:
                    continue
                seen.add(msg_id)

            usage = msg.get("usage") or {}
            if not usage:
                continue

            calls += 1
            model = msg.get("model") or model
            totals["input"] += usage.get("input_tokens") or 0
            totals["output"] += usage.get("output_tokens") or 0
            totals["cache_read"] += usage.get("cache_read_input_tokens") or 0
            totals["cache_creation"] += usage.get("cache_creation_input_tokens") or 0
            details = usage.get("output_tokens_details") or {}
            totals["thinking"] += details.get("thinking_tokens") or 0

    return totals, model, calls


def compute_cost(totals: dict[str, int], model: str) -> str:
    """Cost via the project's own pricing table, or "" if it cannot be priced.

    Returns a string so an unpriceable model writes an empty cell rather than
    a misleading 0.00 -- a zero would read as "this was free".
    """
    try:
        from job_searcher.llm.policy import get_policy
        from job_searcher.llm.pricing import Usage, cost_usd
    except Exception:
        return ""

    try:
        policy = get_policy()
        usage = Usage(
            input_tokens=totals["input"],
            output_tokens=totals["output"],
            cache_read_tokens=totals["cache_read"],
            cache_write_tokens=totals["cache_creation"],
        )
        return f"{cost_usd(usage, normalize_model(model), policy=policy):.6f}"
    except Exception:
        # Most likely an unpriced model (a subagent on a tier the policy does
        # not list). The token counts are still worth recording.
        return ""


def safe_name(value: str) -> str:
    """Filename-safe agent name. agent_type reaches us as data, and a stray
    separator must not write outside cost/."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value or "unknown")
    return cleaned.strip("._") or "unknown"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    transcript = payload.get("agent_transcript_path")
    if not transcript:
        return 0
    path = Path(transcript)
    if not path.exists():
        return 0

    project = Path(payload.get("cwd") or ".")
    agent_type = payload.get("agent_type") or "unknown"

    try:
        totals, model, calls = read_transcript(path)
    except Exception:
        return 0
    if calls == 0:
        return 0

    row = {
        "timestamp_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent_type": agent_type,
        "agent_id": payload.get("agent_id", ""),
        "session_id": payload.get("session_id", ""),
        "model": model,
        "api_calls": calls,
        "input_tokens": totals["input"],
        "output_tokens": totals["output"],
        "cache_read_tokens": totals["cache_read"],
        "cache_creation_tokens": totals["cache_creation"],
        "thinking_tokens": totals["thinking"],
        # Cache reads and writes are billed too, so a "total" that omits them
        # understates a cached agent by an order of magnitude.
        "billable_tokens": (
            totals["input"]
            + totals["output"]
            + totals["cache_read"]
            + totals["cache_creation"]
        ),
        "cost_usd": compute_cost(totals, model),
    }

    try:
        out_dir = project / "cost"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{safe_name(agent_type)}.csv"
        new_file = not out.exists() or out.stat().st_size == 0
        with out.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
    except Exception:
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
