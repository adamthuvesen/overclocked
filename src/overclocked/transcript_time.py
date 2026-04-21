"""Parse ISO-8601 timestamps from jsonl transcript tails for activity gating."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class JsonlTailTimestampResult:
    """Result of scanning the tail of a jsonl file for transcript timestamps."""

    max_unix: float | None
    had_parseable: bool


def parse_iso_timestamp_to_unix(s: str) -> float | None:
    """Parse an ISO-8601 instant to Unix time, or None on failure."""
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t:
        return None
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _extract_line_ts(obj: dict, *, use_payload_timestamp: bool) -> float | None:
    ts = obj.get("timestamp")
    if isinstance(ts, str):
        u = parse_iso_timestamp_to_unix(ts)
        if u is not None:
            return u
    if use_payload_timestamp:
        payload = obj.get("payload")
        if isinstance(payload, dict):
            pts = payload.get("timestamp")
            if isinstance(pts, str):
                return parse_iso_timestamp_to_unix(pts)
    return None


def jsonl_tail_timestamp_result(
    path: Path,
    *,
    tail_bytes: int = 65536,
    max_lines: int = 128,
    use_payload_timestamp: bool = False,
) -> JsonlTailTimestampResult:
    """Scan the last complete lines of a jsonl file; return max parsed timestamp."""
    max_u: float | None = None
    had = False
    try:
        size = path.stat().st_size
    except OSError:
        return JsonlTailTimestampResult(None, False)
    if size == 0:
        return JsonlTailTimestampResult(None, False)
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - tail_bytes))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return JsonlTailTimestampResult(None, False)
    lines = raw.splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]
    lines = lines[-max_lines:]
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        u = _extract_line_ts(obj, use_payload_timestamp=use_payload_timestamp)
        if u is None:
            continue
        had = True
        if max_u is None or u > max_u:
            max_u = u
    return JsonlTailTimestampResult(max_u, had)


def jsonl_transcript_recent(
    path: Path,
    cutoff_unix: float,
    *,
    use_payload_timestamp: bool,
) -> bool:
    """True if tail lacks parseable timestamps (mtime fallback) or max ts >= cutoff_unix."""
    r = jsonl_tail_timestamp_result(path, use_payload_timestamp=use_payload_timestamp)
    if not r.had_parseable:
        return True
    return r.max_unix is not None and r.max_unix >= cutoff_unix
