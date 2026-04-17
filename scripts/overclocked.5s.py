#!/usr/bin/env python3
# <xbar.title>overclocked</xbar.title>
# <xbar.version>v0.1.0</xbar.version>
# <xbar.author>adamthuvesen</xbar.author>
# <xbar.desc>Track active AI coding copilot sessions (Claude Code, Cursor, Codex)</xbar.desc>
# <xbar.dependencies>python3,overclocked</xbar.dependencies>
# <swiftbar.type>streamable</swiftbar.type>

"""SwiftBar streamable plugin entry point.

SwiftBar spawns this script once and keeps it alive; ``overclocked --stream``
emits a full menu followed by a ``~~~`` separator on a fixed cadence. We
``os.execv`` so SwiftBar's process supervision targets the Python interpreter
running the long-lived loop directly (no extra wrapper PID).

Edit ``OVERCLOCKED_BIN`` if your install lives outside the repo's ``.venv``.
"""

import os
import sys
from pathlib import Path

INTERVAL_SEC = "5"

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_VENV_BIN = _REPO_ROOT / ".venv" / "bin" / "overclocked"

OVERCLOCKED_BIN = str(_VENV_BIN) if _VENV_BIN.exists() else "overclocked"


def main() -> None:
    args = [OVERCLOCKED_BIN, "--stream", "--interval", INTERVAL_SEC]
    try:
        os.execv(OVERCLOCKED_BIN, args)
    except FileNotFoundError:
        sys.stdout.write(
            "🧠 ?\n---\n"
            "overclocked not found — check OVERCLOCKED_BIN in scripts/overclocked.5s.py\n"
            "~~~\n"
        )
        sys.stdout.flush()


if __name__ == "__main__":
    main()
