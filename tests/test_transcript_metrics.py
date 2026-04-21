"""Tests for bounded Claude/Codex transcript metric parsers."""

from __future__ import annotations

import json
from pathlib import Path

from overclocked.transcript_metrics import (
    parse_claude_jsonl_tail,
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
    assert snap.output_tokens == 34
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
