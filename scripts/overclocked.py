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
import tempfile
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_VENV_BIN = _REPO_ROOT / ".venv" / "bin" / "overclocked"

OVERCLOCKED_BIN = str(_VENV_BIN) if _VENV_BIN.exists() else "overclocked"


def _first_stderr_line(stderr_file) -> str | None:
    stderr_file.seek(0)
    for line in stderr_file.read().splitlines():
        line = line.strip()
        if line:
            return line
    return None


def _write_error_frame(message: str, detail: str | None = None) -> None:
    if detail:
        sys.stdout.write(f"👾 !\n---\n{message}\n{detail}\n~~~\n")
    else:
        sys.stdout.write(f"👾 !\n---\n{message}\n~~~\n")
    sys.stdout.flush()


def main() -> None:
    emitted = False
    try:
        with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file:
            with subprocess.Popen(
                [OVERCLOCKED_BIN, "--stream"],
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
            ) as proc:
                if proc.stdout is not None:
                    for line in proc.stdout:
                        emitted = True
                        sys.stdout.write(line)
                        sys.stdout.flush()
                return_code = proc.wait()
            if return_code != 0 or not emitted:
                detail = _first_stderr_line(stderr_file)
                _write_error_frame(f"overclocked exited with code {return_code}", detail)
    except FileNotFoundError:
        sys.stdout.write("👾 ?\n---\noverclocked not found — check your .venv\n~~~\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
