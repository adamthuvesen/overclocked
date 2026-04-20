"""Resolve the app's local runtime home."""

from __future__ import annotations

import os
from pathlib import Path

_CANONICAL_ENV = "OVERCLOCKED_HOME"
_CANONICAL_DIR = ".overclocked"


def canonical_runtime_home() -> Path:
    """Return the canonical runtime home under the user's home directory."""
    return Path.home() / _CANONICAL_DIR


def runtime_home() -> Path:
    """Return the runtime home, honoring the OVERCLOCKED_HOME env override."""
    override = os.environ.get(_CANONICAL_ENV)
    if override:
        return Path(override)
    return canonical_runtime_home()
