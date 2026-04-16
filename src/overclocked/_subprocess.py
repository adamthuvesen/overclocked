"""Shared subprocess helper used by detectors and identity layers."""

from __future__ import annotations

import subprocess


def _safe_check_output(args: list[str], *, timeout: int = 2) -> str | None:
    """Run args and return stdout, or None on any failure.

    Catches CalledProcessError, FileNotFoundError, OSError, and TimeoutExpired.
    Always passes timeout, text=True, stderr=DEVNULL, errors="replace".
    """
    try:
        return subprocess.check_output(
            args,
            timeout=timeout,
            text=True,
            stderr=subprocess.DEVNULL,
            errors="replace",
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
