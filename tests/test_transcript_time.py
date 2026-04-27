"""Tests for jsonl transcript timestamp parsing."""

from __future__ import annotations

import json

from overclocked.transcript_time import (
    jsonl_tail_timestamp_result,
    parse_iso_timestamp_to_unix,
)


def test_parse_iso_z_and_fractional():
    u = parse_iso_timestamp_to_unix("2026-04-20T12:49:19.202Z")
    u2 = parse_iso_timestamp_to_unix("2026-04-20T12:49:19.202+00:00")
    assert u is not None and u2 is not None
    assert abs(u - u2) < 1e-6


def test_parse_iso_invalid():
    assert parse_iso_timestamp_to_unix("") is None
    assert parse_iso_timestamp_to_unix("not-a-date") is None
    assert parse_iso_timestamp_to_unix("2026-13-45T99:99:99Z") is None


def test_tail_empty_file(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text("")
    r = jsonl_tail_timestamp_result(f)
    assert not r.had_parseable
    assert r.max_unix is None


def test_tail_single_line(tmp_path):
    f = tmp_path / "x.jsonl"
    f.write_text(json.dumps({"timestamp": "2026-06-01T10:00:00.000Z", "x": 1}) + "\n")
    r = jsonl_tail_timestamp_result(f)
    assert r.had_parseable
    assert r.max_unix is not None


def test_tail_codex_payload_timestamp(tmp_path):
    f = tmp_path / "x.jsonl"
    line = {
        "type": "session_meta",
        "payload": {"timestamp": "2026-06-01T11:00:00.000Z", "cwd": "/p"},
    }
    f.write_text(json.dumps(line) + "\n")
    r = jsonl_tail_timestamp_result(f, use_payload_timestamp=True)
    assert r.had_parseable
    r2 = jsonl_tail_timestamp_result(f, use_payload_timestamp=False)
    assert not r2.had_parseable


def test_tail_partial_line_after_seek(tmp_path):
    f = tmp_path / "x.jsonl"
    long = "x" * 70000
    f.write_text(long + "\n" + json.dumps({"timestamp": "2026-06-01T12:00:00.000Z"}) + "\n")
    r = jsonl_tail_timestamp_result(f, tail_bytes=4096, max_lines=50)
    assert r.had_parseable
