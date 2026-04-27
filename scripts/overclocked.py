#!/usr/bin/env python3
# <xbar.title>overclocked</xbar.title>
# <xbar.version>v0.1.0</xbar.version>
# <xbar.author>adamthuvesen</xbar.author>
# <xbar.desc>Track active AI coding copilot sessions (Claude Code, Cursor, Codex)</xbar.desc>
# <xbar.dependencies>python3,overclocked</xbar.dependencies>
# <swiftbar.type>streamable</swiftbar.type>
# <swiftbar.useTrailingStreamSeparator>true</swiftbar.useTrailingStreamSeparator>

import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_VENV_BIN = _REPO_ROOT / ".venv" / "bin" / "overclocked"

OVERCLOCKED_BIN = str(_VENV_BIN) if _VENV_BIN.exists() else "overclocked"

try:
    with subprocess.Popen([OVERCLOCKED_BIN, "--stream"], stdout=subprocess.PIPE, text=True) as proc:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
except FileNotFoundError:
    sys.stdout.write("👾 ?\n---\noverclocked not found — check your .venv\n~~~\n")
    sys.stdout.flush()
