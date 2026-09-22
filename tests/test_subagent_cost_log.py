"""Tests for the SubagentStop cost-logging hook.

Pure file and string logic -- no network, no API key, no Claude Code session.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

# The hook lives in scripts/, which is not an importable package.
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "log_subagent_cost.py"
_spec = importlib.util.spec_from_file_location("log_subagent_cost", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


# --- task summary ----------------------------------------------------------
def test_short_task_is_unchanged():
    assert mod.summarize_task("Add the Greenhouse adapter") == "Add the Greenhouse adapter"


def test_newlines_are_collapsed():
    """A multi-line cell is valid CSV but makes the file unreadable in a
    terminal and in most diff tools."""
    assert mod.summarize_task("first line\n\n  second   line ") == "first line second line"


def test_long_task_is_truncated_within_the_limit():
    out = mod.summarize_task("x" * 500)
    assert len(out) == mod.TASK_MAX_CHARS == 100
    assert out.endswith("...")


def test_task_exactly_at_the_limit_is_not_truncated():
    exact = "y" * 100
    assert mod.summarize_task(exact) == exact
    assert not mod.summarize_task(exact).endswith("...")


def test_one_over_the_limit_is_truncated():
    assert len(mod.summarize_task("z" * 101)) == 100


def test_empty_task_is_empty():
    assert mod.summarize_task("") == ""
    assert mod.summarize_task(None) == ""


def test_content_blocks_are_flattened():
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "image"},
        {"type": "text", "text": "world"},
    ]
    assert mod._content_text(blocks) == "hello world"


# --- transcript parsing ----------------------------------------------------
def _write_transcript(tmp_path: Path, entries: list[dict]) -> Path:
    p = tmp_path / "agent.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    return p


def _assistant(msg_id: str, **usage) -> dict:
    base = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    base.update(usage)
    return {
        "type": "assistant",
        "message": {"id": msg_id, "model": "claude-haiku-4-5-20251001", "usage": base},
    }


def test_duplicate_message_ids_are_counted_once(tmp_path):
    """One API response can span several transcript lines -- separate content
    blocks sharing a message.id, each repeating the same aggregate usage.
    Summing every line doubles the numbers."""
    t = _write_transcript(
        tmp_path,
        [
            _assistant("msg_1", input_tokens=10, output_tokens=47),
            _assistant("msg_1", input_tokens=10, output_tokens=47),
        ],
    )
    totals, model, calls = mod.read_transcript(t)
    assert calls == 1
    assert totals["input"] == 10
    assert totals["output"] == 47
    assert model == "claude-haiku-4-5-20251001"


def test_last_occurrence_of_a_message_wins(tmp_path):
    """Repeated lines for one response do NOT always carry the same usage: a
    partial count can be followed by the completed one (observed in a real
    transcript as output_tokens 1 then 150). Keeping the first occurrence
    undercounts output."""
    t = _write_transcript(
        tmp_path,
        [
            _assistant("msg_1", input_tokens=10, output_tokens=1),
            _assistant("msg_1", input_tokens=10, output_tokens=150),
        ],
    )
    totals, _, calls = mod.read_transcript(t)
    assert calls == 1
    assert totals["output"] == 150


def test_totals_are_cumulative_across_calls(tmp_path):
    """Every call re-sends the conversation and pays to read the cache again,
    so cache_read accumulates. This is billed spend, not context size."""
    t = _write_transcript(
        tmp_path,
        [
            _assistant("a", cache_creation_input_tokens=25415, output_tokens=150),
            _assistant(
                "b",
                cache_read_input_tokens=25415,
                cache_creation_input_tokens=2442,
                output_tokens=116,
            ),
        ],
    )
    totals, _, calls = mod.read_transcript(t)
    assert calls == 2
    assert totals["cache_read"] == 25415
    assert totals["cache_creation"] == 27857
    assert totals["output"] == 266


def test_entries_without_an_id_are_each_counted(tmp_path):
    t = _write_transcript(
        tmp_path,
        [
            {"type": "assistant", "message": {"usage": {"output_tokens": 3}}},
            {"type": "assistant", "message": {"usage": {"output_tokens": 4}}},
        ],
    )
    totals, _, calls = mod.read_transcript(t)
    assert calls == 2 and totals["output"] == 7


def test_distinct_messages_are_summed(tmp_path):
    t = _write_transcript(
        tmp_path,
        [_assistant("a", output_tokens=5), _assistant("b", output_tokens=7)],
    )
    totals, _, calls = mod.read_transcript(t)
    assert calls == 2
    assert totals["output"] == 12


def test_malformed_line_does_not_abort_parsing(tmp_path):
    p = tmp_path / "agent.jsonl"
    p.write_text(
        json.dumps(_assistant("a", output_tokens=5)) + "\n{ truncated",
        encoding="utf-8",
    )
    totals, _, calls = mod.read_transcript(p)
    assert calls == 1 and totals["output"] == 5


def test_read_task_takes_the_first_user_message(tmp_path):
    t = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"content": "Diagnose the failing Lever source"}},
            _assistant("a", output_tokens=1),
            {"type": "user", "message": {"content": "a later message"}},
        ],
    )
    assert mod.read_task(t) == "Diagnose the failing Lever source"


# --- model normalisation ---------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),
        ("claude-opus-5", "claude-opus-5"),
        ("claude-opus-5[1m]", "claude-opus-5"),
        ("", ""),
    ],
)
def test_model_ids_normalise_to_pricing_keys(raw, expected):
    assert mod.normalize_model(raw) == expected


# --- CSV header migration --------------------------------------------------
def test_migration_preserves_existing_rows_and_realigns(tmp_path):
    """Appending a wider row to a file with an older header silently
    misaligns every value after the new column."""
    p = tmp_path / "general-purpose.csv"
    old_cols = [c for c in mod.COLUMNS if c != "task"]
    with p.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=old_cols)
        w.writeheader()
        w.writerow(
            dict.fromkeys(old_cols, "")
            | {"agent_type": "adapter-doctor", "output_tokens": "47"}
        )

    mod.migrate_header(p)

    with p.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == mod.COLUMNS
    assert rows[0]["task"] == ""                     # back-filled, not shifted
    assert rows[0]["agent_type"] == "adapter-doctor"  # still aligned
    assert rows[0]["output_tokens"] == "47"


def test_migration_is_a_noop_when_the_header_matches(tmp_path):
    p = tmp_path / "x.csv"
    with p.open("w", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=mod.COLUMNS).writeheader()
    before = p.read_bytes()
    mod.migrate_header(p)
    assert p.read_bytes() == before


def test_migration_ignores_a_missing_file(tmp_path):
    mod.migrate_header(tmp_path / "nope.csv")  # must not raise


# --- filename safety -------------------------------------------------------
def test_agent_type_cannot_escape_the_cost_directory():
    assert "/" not in mod.safe_name("../../etc/passwd")
    assert mod.safe_name("") == "unknown"
