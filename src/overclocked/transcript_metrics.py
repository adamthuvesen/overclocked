"""Parse Claude and Codex jsonl tails for model + token usage (bounded reads)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

MAX_LINE_BYTES = 10 * 1024 * 1024
DEFAULT_TAIL_BYTES = 65536
DEFAULT_MAX_LINES = 160
CLAUDE_PROJECT_TRANSCRIPT_LIMIT = 64
CLAUDE_PROJECT_TRANSCRIPT_WALK_LIMIT = 512


@dataclass
class UsageSnapshot:
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read: int | None = None
    cache_create: int | None = None


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
    active_session: str | None = None
    for line in lines:
        line = line.strip()
        if not line or len(line) > MAX_LINE_BYTES:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        sid = obj.get("sessionId") or obj.get("session_id")
        if isinstance(sid, str) and sid.strip():
            active_session = sid.strip()
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
        row_sid = obj.get("sessionId") or obj.get("session_id")
        if active_session is not None and isinstance(row_sid, str) and row_sid.strip():
            if row_sid.strip() != active_session:
                continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        snap = _claude_usage_from_message(msg)
        if snap is not None:
            best = snap
    return best


def claude_project_transcript_candidates(
    proj: Path,
    *,
    limit: int = CLAUDE_PROJECT_TRANSCRIPT_LIMIT,
) -> list[Path]:
    """Return bounded parent transcript candidates for a Claude project dir, newest first."""
    scored: dict[Path, float] = {}
    walked = 0

    def consider(path: Path) -> None:
        if "subagents" in path.parts:
            return
        try:
            if not path.is_file():
                return
            scored[path] = path.stat().st_mtime
        except OSError:
            return

    consider(proj / "conversation.jsonl")
    try:
        for path in proj.glob("*.jsonl"):
            walked += 1
            if walked > CLAUDE_PROJECT_TRANSCRIPT_WALK_LIMIT:
                break
            consider(path)
    except OSError:
        pass
    if walked <= CLAUDE_PROJECT_TRANSCRIPT_WALK_LIMIT:
        try:
            for path in proj.rglob("agent-*.jsonl"):
                walked += 1
                if walked > CLAUDE_PROJECT_TRANSCRIPT_WALK_LIMIT:
                    break
                consider(path)
        except OSError:
            pass
    return [
        path
        for path, _mtime in sorted(
            scored.items(),
            key=lambda item: (-item[1], str(item[0])),
        )[:limit]
    ]


def parse_claude_project_dir(proj: Path) -> UsageSnapshot:
    """Usage snapshot from the most recently modified transcript (active thread).

    Previously we merged by highest token total, which let stale ``agent-*.jsonl``
    rows dominate over a freshly started ``conversation.jsonl``.
    """
    candidates = claude_project_transcript_candidates(proj)
    if not candidates:
        return UsageSnapshot()
    return parse_claude_jsonl_tail(candidates[0])


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
    parsed: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line or len(line) > MAX_LINE_BYTES:
            continue
        try:
            val = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            parsed.append(val)
    idx_last_turn = -1
    for i, val in enumerate(parsed):
        if val.get("type") == "turn_context":
            idx_last_turn = i
    # Ignore token_count lines from before the last turn_context in this window.
    # Otherwise after a new turn/session, stale totals linger until a new token_count arrives.
    start = idx_last_turn if idx_last_turn >= 0 else 0
    snap = UsageSnapshot()
    for val in parsed[start:]:
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
            # Prefer last_token_usage (current-turn context snapshot) over
            # total_token_usage (cumulative session sum). This matches what
            # Codex shows as context and aligns with abtop's context_tokens.
            last = info.get("last_token_usage")
            total = info.get("total_token_usage")
            src = last if isinstance(last, dict) else (total if isinstance(total, dict) else None)
            if src is None:
                continue
            try:
                inp = int(src.get("input_tokens") or 0)
                cr = int(
                    src.get("cached_input_tokens") or src.get("cache_read_input_tokens") or 0,
                )
                cc = int(src.get("cache_creation_input_tokens") or 0)
            except (TypeError, ValueError):
                continue
            snap.input_tokens = inp
            snap.cache_read = cr
            snap.cache_create = cc
    return snap
