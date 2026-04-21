"""Parse Claude and Codex jsonl tails for model + token usage (bounded reads)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


MAX_LINE_BYTES = 10 * 1024 * 1024
DEFAULT_TAIL_BYTES = 65536
DEFAULT_MAX_LINES = 160


@dataclass
class UsageSnapshot:
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read: int | None = None
    cache_create: int | None = None


def _token_total(s: UsageSnapshot) -> int:
    a = s.input_tokens or 0
    b = s.output_tokens or 0
    c = s.cache_read or 0
    d = s.cache_create or 0
    return a + b + c + d


def _merge_better(current: UsageSnapshot, candidate: UsageSnapshot) -> UsageSnapshot:
    """Prefer snapshot with higher cumulative token total."""
    if _token_total(candidate) >= _token_total(current):
        return UsageSnapshot(
            model=candidate.model or current.model,
            input_tokens=candidate.input_tokens,
            output_tokens=candidate.output_tokens,
            cache_read=candidate.cache_read,
            cache_create=candidate.cache_create,
        )
    return current


def _claude_usage_from_message(msg: dict) -> UsageSnapshot | None:
    if not isinstance(msg, dict):
        return None
    model = msg.get("model")
    model_s = model if isinstance(model, str) and model else None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        if model_s:
            return UsageSnapshot(model=model_s)
        return None
    try:
        inp = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        cr = int(usage.get("cache_read_input_tokens") or 0)
        cc = int(usage.get("cache_creation_input_tokens") or 0)
    except (TypeError, ValueError):
        return UsageSnapshot(model=model_s) if model_s else None
    if not model_s and inp == 0 and out == 0 and cr == 0 and cc == 0:
        return None
    return UsageSnapshot(
        model=model_s,
        input_tokens=inp,
        output_tokens=out,
        cache_read=cr,
        cache_create=cc,
    )


def parse_claude_jsonl_tail(
    path: Path,
    *,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> UsageSnapshot:
    """Scan jsonl tail for the last assistant message with usage (Claude Code / desktop)."""
    best = UsageSnapshot()
    try:
        size = path.stat().st_size
    except OSError:
        return best
    if size == 0:
        return best
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - tail_bytes))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return best
    lines = raw.splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]
    lines = lines[-max_lines:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line) > MAX_LINE_BYTES:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        snap = _claude_usage_from_message(msg)
        if snap is not None:
            best = snap
    return best


def parse_claude_project_dir(proj: Path) -> UsageSnapshot:
    """Aggregate best usage snapshot from conversation.jsonl and agent-*.jsonl."""
    best = UsageSnapshot()
    candidates: list[Path] = []
    conv = proj / "conversation.jsonl"
    if conv.is_file():
        candidates.append(conv)
    try:
        for p in proj.rglob("agent-*.jsonl"):
            try:
                if p.is_file():
                    candidates.append(p)
            except OSError:
                pass
    except OSError:
        pass
    for p in candidates[:48]:
        snap = parse_claude_jsonl_tail(p)
        best = _merge_better(best, snap)
    return best


def parse_codex_rollout_tail(
    path: Path,
    *,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> UsageSnapshot:
    """Scan Codex rollout jsonl tail for latest model and token_count totals."""
    snap = UsageSnapshot()
    try:
        size = path.stat().st_size
    except OSError:
        return snap
    if size == 0:
        return snap
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - tail_bytes))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return snap
    lines = raw.splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]
    lines = lines[-max_lines:]
    for line in lines:
        line = line.strip()
        if not line or len(line) > MAX_LINE_BYTES:
            continue
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(val, dict):
            continue
        t = val.get("type")
        if t == "turn_context":
            payload = val.get("payload")
            if isinstance(payload, dict):
                m = payload.get("model")
                if isinstance(m, str) and m:
                    snap.model = m
        elif t == "event_msg":
            payload = val.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total = info.get("total_token_usage")
            if not isinstance(total, dict):
                continue
            try:
                inp = int(total.get("input_tokens") or 0)
                out = int(total.get("output_tokens") or 0)
                cr = int(
                    total.get("cached_input_tokens")
                    or total.get("cache_read_input_tokens")
                    or 0,
                )
                cc = int(total.get("cache_creation_input_tokens") or 0)
            except (TypeError, ValueError):
                continue
            snap.input_tokens = inp
            snap.output_tokens = out
            snap.cache_read = cr
            snap.cache_create = cc
    return snap
