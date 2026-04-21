"""Persist raw sampler keys between SwiftBar invocations for cross-process debounce."""

from __future__ import annotations

import json
import os
import tempfile
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
    data = json.dumps(payload)
    # Unique temp name: concurrent ``overclocked --once`` (e.g. menubar timer + Refresh)
    # used to share ``sampler-state.tmp``; the first ``os.replace`` removed it and the
    # second raised [Errno 2] No such file or directory.
    fd, tmp_name = tempfile.mkstemp(prefix="sampler-state.", suffix=".tmp", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
