"""Resolve the app's local runtime home."""

from __future__ import annotations

import os
from pathlib import Path

_HOME_ENV = "OVERCLOCKED_HOME"
_HOME_DIR = ".overclocked"


def runtime_home() -> Path:
    """Return the runtime home, honoring the OVERCLOCKED_HOME env override."""
    override = os.environ.get(_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / _HOME_DIR
