#!/usr/bin/env python3
# <xbar.title>overclocked</xbar.title>
# <xbar.version>v0.1.0</xbar.version>
# <xbar.author>adamthuvesen</xbar.author>
# <xbar.desc>Track active AI coding copilot sessions (Claude Code, Cursor, Codex)</xbar.desc>
# <xbar.dependencies>python3,overclocked</xbar.dependencies>

import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_VENV_BIN = _REPO_ROOT / ".venv" / "bin" / "overclocked"

OVERCLOCKED_BIN = str(_VENV_BIN) if _VENV_BIN.exists() else "overclocked"

result = subprocess.run([OVERCLOCKED_BIN, "--once"], capture_output=True, text=True)
sys.stdout.write(result.stdout if result.returncode == 0 else "🧠 ?\n---\noverclocked not found\n")
