"""Resolve working directory and project label for a detected session."""

from __future__ import annotations

from pathlib import Path

from overclocked._subprocess import _safe_check_output
from overclocked.config import Config

_LOF_CHUNK = 64


def _worktree_root_name(path: Path) -> str | None:
    """Collapse managed worktree paths back to their source repo name."""
    parts = path.parts
    for marker in (".claude", ".codex", ".cursor"):
        try:
            idx = parts.index(marker)
        except ValueError:
            continue
        if idx >= 1 and idx + 1 < len(parts) and parts[idx + 1] == "worktrees":
            return parts[idx - 1] or None
    return None


def _parse_lsof_fn_cwd_blocks(text: str) -> dict[int, str | None]:
    """Parse `lsof -Fn` for cwd lines; map pid -> cwd path."""
    result: dict[int, str | None] = {}
    cur_pid: int | None = None
    expect_path = False
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("p") and line[1:].strip().isdigit():
            cur_pid = int(line[1:].strip())
            expect_path = False
        elif line.startswith("fcwd"):
            expect_path = True
        elif line.startswith("n") and expect_path and cur_pid is not None:
            result[cur_pid] = line[1:].strip() or None
            expect_path = False
    return result


def resolve_cwd(pid: int) -> str | None:
    """Return the working directory of pid via lsof, or None if unavailable."""
    out = _safe_check_output(
        ["lsof", "-p", str(pid), "-a", "-d", "cwd", "-Fn"],
        timeout=2,
    )
    if out is None:
        return None
    for line in out.splitlines():
        if line.startswith("n"):
            return line[1:].strip()
    return None


def resolve_cwds_batch(pids: list[int], *, timeout: int = 2) -> dict[int, str | None]:
    """Resolve cwds for many pids; chunk lsof -p to stay under argv limits."""
    if not pids:
        return {}
    result: dict[int, str | None] = {}
    uniq = sorted(set(pids))
    for i in range(0, len(uniq), _LOF_CHUNK):
        chunk = uniq[i : i + _LOF_CHUNK]
        arg = ",".join(str(p) for p in chunk)
        out = _safe_check_output(
            ["lsof", "-p", arg, "-a", "-d", "cwd", "-Fn"],
            timeout=timeout,
        )
        if out is None:
            for p in chunk:
                result[p] = resolve_cwd(p)
            continue
        parsed = _parse_lsof_fn_cwd_blocks(out)
        for p in chunk:
            if p in parsed:
                result[p] = parsed[p]
            else:
                result[p] = resolve_cwd(p)
    return result


def project_label(cwd: str | None, config: Config) -> str | None:
    """Return a human label for cwd, applying redaction, or None if cwd is None."""
    if cwd is None:
        return None
    if config.is_redacted(cwd):
        return "redacted"
    path = Path(cwd)
    worktree_name = _worktree_root_name(path)
    if worktree_name is not None:
        return worktree_name
    return path.name or None


def session_key(tool: str, cwd: str | None, pid: int) -> str:
    """Return a stable identity string for a session.

    Uses (tool, cwd) when cwd is available so the key survives minor PID
    changes (e.g. shell restart in same directory). Falls back to pid.
    """
    if cwd:
        return f"{tool}:{cwd}"
    return f"{tool}:pid:{pid}"
