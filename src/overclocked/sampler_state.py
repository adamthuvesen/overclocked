"""Persist raw sampler keys between SwiftBar invocations for cross-process debounce."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from overclocked.runtime_home import runtime_home

_STATE_VERSION = 1
_FILENAME = "sampler-state.json"


def sampler_state_path() -> Path:
    return runtime_home() / _FILENAME


def load_raw_session_keys() -> frozenset[tuple[str, int]] | None:
    """Return persisted (tool, pid) keys, or None if missing or invalid."""
    path = sampler_state_path()
    if not path.exists():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("version") != _STATE_VERSION:
        return None
    keys = data.get("keys")
    if not isinstance(keys, list):
        return None
    out: list[tuple[str, int]] = []
    for item in keys:
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], int)
        ):
            out.append((item[0], item[1]))
        else:
            return None
    return frozenset(out)


def save_raw_session_keys(keys: frozenset[tuple[str, int]]) -> None:
    """Atomically persist raw keys for the next invocation."""
    home = runtime_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / _FILENAME
    payload = {"version": _STATE_VERSION, "keys": [list(k) for k in sorted(keys)]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, path)
