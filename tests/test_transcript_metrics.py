"""Tests for bounded Claude/Codex transcript metric parsers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from overclocked.transcript_metrics import (
    parse_claude_jsonl_tail,
    parse_claude_project_dir,
    parse_codex_rollout_tail,
)


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")


def test_parse_claude_jsonl_tail_last_assistant_usage(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "user", "message": {}},
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4-20250514",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "cache_read_input_tokens": 2000,
                        "cache_creation_input_tokens": 0,
                    },
                },
            },
        ],
    )
    snap = parse_claude_jsonl_tail(p)
    assert snap.model == "claude-sonnet-4-20250514"
    assert snap.input_tokens == 100
    assert snap.output_tokens == 40
    assert snap.cache_read == 2000
    assert snap.cache_create == 0


def test_parse_claude_jsonl_tail_prefers_last_session_id(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "assistant",
                "sessionId": "old",
                "message": {
                    "model": "claude-old",
                    "usage": {"input_tokens": 99_000, "output_tokens": 0},
                },
            },
            {
                "type": "assistant",
                "sessionId": "new",
                "message": {
                    "model": "claude-new",
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            },
        ],
    )
    snap = parse_claude_jsonl_tail(p)
    assert snap.model == "claude-new"
    assert snap.input_tokens == 3


def test_parse_claude_project_dir_prefers_newest_transcript_file(tmp_path: Path) -> None:
    proj = tmp_path / "p"
    proj.mkdir()
    stale = proj / "agent-stale.jsonl"
    fresh = proj / "conversation.jsonl"
    _write_jsonl(
        stale,
        [
            {
                "type": "assistant",
                "message": {
                    "model": "m",
                    "usage": {"input_tokens": 9_000_000, "output_tokens": 0},
                },
            },
        ],
    )
    _write_jsonl(
        fresh,
        [
            {
                "type": "assistant",
                "message": {
                    "model": "m2",
                    "usage": {"input_tokens": 12, "output_tokens": 0},
                },
            },
        ],
    )
    old_t = time.time() - 100.0
    os.utime(stale, (old_t, old_t))
    os.utime(fresh, (time.time(), time.time()))
    snap = parse_claude_project_dir(proj)
    assert snap.input_tokens == 12


def test_parse_codex_rollout_tail_turn_context_and_token_count(tmp_path: Path) -> None:
    p = tmp_path / "rollout.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "turn_context", "payload": {"model": "gpt-5.2"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 12,
                            "output_tokens": 34,
                            "cached_input_tokens": 56,
                            "cache_creation_input_tokens": 7,
                        },
                    },
                },
            },
        ],
    )
    snap = parse_codex_rollout_tail(p)
    assert snap.model == "gpt-5.2"
    assert snap.input_tokens == 12
    assert snap.cache_read == 56
    assert snap.cache_create == 7


def test_parse_codex_cache_read_field_variant(tmp_path: Path) -> None:
    p = tmp_path / "roll.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1,
                            "output_tokens": 2,
                            "cache_read_input_tokens": 99,
                        },
                    },
                },
            },
        ],
    )
    snap = parse_codex_rollout_tail(p)
    assert snap.cache_read == 99


def test_parse_codex_rollout_tail_drops_stale_token_counts_after_turn_context(
    tmp_path: Path,
) -> None:
    """After a new turn_context, ignore older token_count rows still in the tail window."""
    p = tmp_path / "rollout.jsonl"
    _write_jsonl(
        p,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 7_800_000,
                            "output_tokens": 0,
                        },
                    },
                },
            },
            {"type": "turn_context", "payload": {"model": "gpt-5.4"}},
        ],
    )
    snap = parse_codex_rollout_tail(p)
    assert snap.model == "gpt-5.4"
    assert snap.input_tokens is None


def test_parse_codex_rollout_tail_prefers_last_token_usage_over_total(tmp_path: Path) -> None:
    """Prefer last_token_usage (current turn) over total_token_usage (cumulative)."""
    p = tmp_path / "rollout.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "turn_context", "payload": {"model": "gpt-5.4"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 999_000,
                            "output_tokens": 5000,
                            "cached_input_tokens": 800_000,
                        },
                        "last_token_usage": {
                            "input_tokens": 2000,
                            "cached_input_tokens": 35000,
                        },
                    },
                },
            },
        ],
    )
    snap = parse_codex_rollout_tail(p)
    assert snap.input_tokens == 2000
    assert snap.cache_read == 35000


def test_parse_codex_rollout_tail_falls_back_to_total_when_no_last(tmp_path: Path) -> None:
    """When last_token_usage is absent, fall back to total_token_usage."""
    p = tmp_path / "rollout.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "turn_context", "payload": {"model": "gpt-5.4"}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 12000,
                            "cached_input_tokens": 25000,
                        },
                    },
                },
            },
        ],
    )
    snap = parse_codex_rollout_tail(p)
    assert snap.input_tokens == 12000
    assert snap.cache_read == 25000
