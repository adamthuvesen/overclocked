"""Resolve and migrate the app's local runtime home."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_CANONICAL_ENV = "OVERCLOCKED_HOME"
_LEGACY_ENV = "QUORUM_HOME"
_CANONICAL_DIR = ".overclocked"
_LEGACY_DIR = ".quorum"
_MIGRATED_FILES = (
    "config.toml",
    "history.db",
    "history.db-wal",
    "history.db-shm",
    "error.log",
)


def canonical_runtime_home() -> Path:
    """Return the canonical runtime home under the user's home directory."""
    return Path.home() / _CANONICAL_DIR


def legacy_runtime_home() -> Path:
    """Return the legacy runtime home used before the rename."""
    return Path.home() / _LEGACY_DIR


def runtime_home() -> Path:
    """Return the runtime home, honoring env overrides and legacy migration."""
    override = os.environ.get(_CANONICAL_ENV)
    if override:
        return Path(override)

    legacy_override = os.environ.get(_LEGACY_ENV)
    if legacy_override:
        return Path(legacy_override)

    home = canonical_runtime_home()
    _adopt_legacy_files(home)
    return home


def _adopt_legacy_files(canonical_home: Path) -> None:
    """Move known legacy runtime files into the canonical home when missing."""
    legacy_home = legacy_runtime_home()
    legacy_files = [name for name in _MIGRATED_FILES if (legacy_home / name).exists()]
    if not legacy_files:
        return

    canonical_home.mkdir(parents=True, exist_ok=True)
    _move_if_missing(legacy_home, canonical_home, "config.toml")
    _adopt_history_db(legacy_home, canonical_home)
    _move_if_missing(legacy_home, canonical_home, "error.log")


def _adopt_history_db(legacy_home: Path, canonical_home: Path) -> None:
    """Move the legacy history database as one logical unit when canonical is absent."""
    legacy_db = legacy_home / "history.db"
    canonical_db = canonical_home / "history.db"
    if not legacy_db.exists() or canonical_db.exists():
        return

    _move_if_missing(legacy_home, canonical_home, "history.db")
    _move_if_missing(legacy_home, canonical_home, "history.db-wal")
    _move_if_missing(legacy_home, canonical_home, "history.db-shm")


def _move_if_missing(src_dir: Path, dst_dir: Path, name: str) -> None:
    """Move one file into the canonical home when the destination is absent."""
    src = src_dir / name
    dst = dst_dir / name
    if not src.exists() or dst.exists():
        return
    shutil.move(str(src), str(dst))
