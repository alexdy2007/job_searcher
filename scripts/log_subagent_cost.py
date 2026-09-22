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

#: Longest task summary written to the CSV, including the ellipsis.
TASK_MAX_CHARS = 100

COLUMNS = [
    "timestamp_utc",
    "agent_type",
    "task",
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


def _content_text(content) -> str:
    """Flatten a message content field, which is a plain string for a simple
    prompt and a list of blocks once attachments are involved."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def summarize_task(text: str, limit: int = TASK_MAX_CHARS) -> str:
    """Collapse a prompt to a single short line for the CSV.

    Newlines are collapsed rather than kept: a prompt spanning lines would
    otherwise be quoted into a multi-line CSV cell, which is valid but makes
    the file unreadable in a terminal and in most diff tools.
    """
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 3)].rstrip() + "..."


def read_task(path: Path) -> str:
    """The prompt the subagent was given: the first user entry in its
    transcript. The Agent tool's own `description` argument is not recorded
    anywhere in the payload or the transcript, so this is the closest
    available statement of the task."""
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "user":
                    continue
                text = _content_text((entry.get("message") or {}).get("content"))
                if text.strip():
                    return summarize_task(text)
    except Exception:
        pass
    return ""


def read_transcript(path: Path) -> tuple[dict[str, int], str, int]:
    """Sum usage across a subagent transcript, one entry per API response.

    Deduping by ``message.id`` is necessary because one response occupies
    several transcript lines. **Last occurrence wins**, not first: those lines
    do not always repeat the same usage block. A response can appear first
    with a partial count and again with the completed one (observed:
    ``output_tokens`` 1 then 150 for the same id), so keeping the first
    occurrence undercounts output.

    Totals are cumulative across calls, which is what is billed -- every call
    re-sends the conversation and pays to read the cache again. This is
    deliberately *not* the same measure as the context size of the final call.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    model = ""

    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh):
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
            usage = msg.get("usage") or {}
            if not usage:
                continue

            # An entry with no id cannot be deduped; count it on its own.
            key = msg.get("id") or f"__line_{n}"
            if key not in by_id:
                order.append(key)
            by_id[key] = usage
            model = msg.get("model") or model

    totals = dict.fromkeys(
        ["input", "output", "cache_read", "cache_creation", "thinking"], 0
    )
    for key in order:
        usage = by_id[key]
        totals["input"] += usage.get("input_tokens") or 0
        totals["output"] += usage.get("output_tokens") or 0
        totals["cache_read"] += usage.get("cache_read_input_tokens") or 0
        totals["cache_creation"] += usage.get("cache_creation_input_tokens") or 0
        details = usage.get("output_tokens_details") or {}
        totals["thinking"] += details.get("thinking_tokens") or 0

    return totals, model, len(order)


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


def migrate_header(path: Path) -> None:
    """Rewrite an existing CSV whose header predates a column change.

    DictWriter only writes a header for a new file, so appending a wider row
    to a file with an older header silently misaligns every value after the
    added column. Rewriting in place, back-filling the new fields as empty,
    keeps the rows already collected rather than discarding or corrupting
    them.
    """
    if not path.exists() or path.stat().st_size == 0:
        return
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fh.seek(0)
        header = next(csv.reader(fh), [])

    if header == COLUMNS:
        return

    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for old in rows:
            writer.writerow({col: old.get(col, "") for col in COLUMNS})
    tmp.replace(path)


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
        "task": read_task(path),
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
        migrate_header(out)
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
