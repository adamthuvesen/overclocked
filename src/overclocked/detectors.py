"""Process-level detection of active AI coding sessions."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from overclocked._subprocess import _safe_check_output
from overclocked.config import Config
from overclocked.identity import project_label, resolve_cwd, resolve_cwds_batch
from overclocked.transcript_metrics import parse_claude_jsonl_tail, parse_claude_project_dir, parse_codex_rollout_tail
from overclocked.transcript_time import jsonl_tail_timestamp_result, jsonl_transcript_recent


@dataclass
class Session:
    tool: str  # 'claude' | 'cursor_editor' | 'cursor_agent' | 'codex'
    pid: int
    cwd: str | None = None
    project: str | None = None
    #: ``working`` | ``waiting`` | ``done`` (abtop-style); ``None`` when unknown
    status: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read: int | None = None
    cache_create: int | None = None
    #: Desktop / file-backed session jsonl for metrics (Claude desktop, Codex desktop)
    transcript_path: Path | None = None


@dataclass(frozen=True)
class PsRow:
    ppid: int
    tty: str
    pcpu: float
    command: str


@dataclass(frozen=True)
class CodexTickData:
    cli_active_cwds: frozenset[str]
    #: Normalised cwd → rollout jsonl path (newest mtime wins per cwd)
    cli_rollout_by_cwd: dict[str, Path]
    desktop_rows: list[tuple[Path, float, str | None]]


# Process table for current tick (None → fall back to per-pid ps)
_ps_table: dict[int, PsRow] | None = None
_codex_tick_data: CodexTickData | None = None

_CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
_CLAUDE_CLI_COMBINED_PATTERN = r"(^|/)claude(-code)?( |$)|@anthropic-ai/claude-code"
_CURSOR_PROJECTS_DIR = Path.home() / ".cursor" / "projects"
_CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
_ACTIVITY_WINDOW_SEC = 5 * 60
_CPU_ACTIVITY_THRESHOLD = 5.0
# abtop-style session status (transcript recency + CPU + tool children)
_STATUS_RECENCY_SEC = 30
_STATUS_PARENT_CPU_PCT = 1.0
_STATUS_DESCENDANT_CPU_PCT = 5.0

_cwd_cache: dict[int, str | None] = {}
_mtime_cache: dict[str, float] = {}


def _begin_tick() -> None:
    """Reset per-tick caches and load a batched ps snapshot."""
    global _ps_table, _codex_tick_data
    _cwd_cache.clear()
    _mtime_cache.clear()
    _codex_tick_data = None
    _ps_table = _load_ps_snapshot()


def _load_ps_snapshot() -> dict[int, PsRow] | None:
    out = _safe_check_output(
        ["ps", "-axo", "pid=,ppid=,tty=,pcpu=,command="],
        timeout=8,
    )
    if out is None:
        return None
    return _parse_ps_axo(out)


def _parse_ps_axo(output: str) -> dict[int, PsRow]:
    table: dict[int, PsRow] = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pcpu = float(parts[3])
        except ValueError:
            continue
        tty, cmd = parts[2], parts[4]
        table[pid] = PsRow(ppid=ppid, tty=tty, pcpu=pcpu, command=cmd)
    return table


def _pgrep(pattern: str) -> list[int]:
    """Return PIDs matching pgrep -f pattern, or [] on failure. Order-stable dedupe."""
    out = _safe_check_output(["pgrep", "-f", pattern])
    if out is None:
        return []
    return list(dict.fromkeys(int(p) for p in out.strip().splitlines() if p.strip()))


def _has_tty(pid: int) -> bool:
    if _ps_table and pid in _ps_table:
        tty = _ps_table[pid].tty.strip()
        return bool(tty) and tty != "??"
    out = _safe_check_output(["ps", "-p", str(pid), "-o", "tty="])
    if out is None:
        return False
    stripped = out.strip()
    return bool(stripped) and stripped != "??"


def _argv(pid: int) -> str:
    if _ps_table and pid in _ps_table:
        return _ps_table[pid].command
    out = _safe_check_output(["ps", "-p", str(pid), "-o", "command="])
    return out.strip() if out is not None else ""


def _ppid(pid: int) -> int | None:
    if _ps_table and pid in _ps_table:
        return _ps_table[pid].ppid
    out = _safe_check_output(["ps", "-p", str(pid), "-o", "ppid="])
    if out is None:
        return None
    try:
        return int(out.strip())
    except ValueError:
        return None


def _cpu_percent(pid: int) -> float:
    if _ps_table and pid in _ps_table:
        return _ps_table[pid].pcpu
    out = _safe_check_output(["ps", "-p", str(pid), "-o", "%cpu="])
    if out is None:
        return 0.0
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _ps_info(pid: int) -> tuple[str, int] | None:
    if _ps_table and pid in _ps_table:
        row = _ps_table[pid]
        return row.command, row.ppid
    out = _safe_check_output(["ps", "-p", str(pid), "-o", "ppid=,command="])
    if out is None:
        return None
    stripped = out.strip()
    if not stripped:
        return None
    parts = stripped.split(None, 1)
    if len(parts) < 2:
        return None
    try:
        return parts[1], int(parts[0])
    except ValueError:
        return None


def is_descendant_of(pid: int, names: list[str]) -> bool:
    """Return True if any ancestor process has a name matching names."""
    if _ps_table:
        visited: set[int] = set()
        current = pid
        while current and current not in visited:
            visited.add(current)
            row = _ps_table.get(current)
            if row is None:
                break
            if any(name.lower() in row.command.lower() for name in names):
                return True
            parent = row.ppid
            if parent <= 1 or parent == current:
                break
            current = parent
        return False

    visited = set()
    current = pid
    while current and current not in visited:
        visited.add(current)
        info = _ps_info(current)
        if info is None:
            break
        cmd, parent = info
        if any(name.lower() in cmd.lower() for name in names):
            return True
        if parent is None or parent == current or parent <= 1:
            break
        current = parent
    return False


def _children_by_parent_from_ps() -> dict[int, list[int]]:
    """Map ppid → child pids from the current ``_ps_table`` snapshot."""
    if not _ps_table:
        return {}
    children: dict[int, list[int]] = {}
    for pid, row in _ps_table.items():
        children.setdefault(row.ppid, []).append(pid)
    return children


def has_active_descendant(root_pid: int, cpu_threshold: float) -> bool:
    """True if any descendant of ``root_pid`` exceeds ``cpu_threshold`` %CPU."""
    if not _ps_table:
        return False
    children_by_parent = _children_by_parent_from_ps()
    stack = list(children_by_parent.get(root_pid, []))
    visited: set[int] = set()
    while stack:
        cpid = stack.pop()
        if cpid in visited:
            continue
        visited.add(cpid)
        row = _ps_table.get(cpid)
        if row is not None and row.pcpu > cpu_threshold:
            return True
        stack.extend(children_by_parent.get(cpid, []))
    return False


def _working_or_waiting_from_signals(
    pid: int,
    last_activity_unix: float | None,
    *,
    now: float | None = None,
) -> str:
    """abtop-style Working vs Waiting from transcript age and CPU (live PID)."""
    t = time.time() if now is None else now
    if last_activity_unix is not None and (t - last_activity_unix) < _STATUS_RECENCY_SEC:
        return "working"
    if _cpu_percent(pid) > _STATUS_PARENT_CPU_PCT:
        return "working"
    if has_active_descendant(pid, _STATUS_DESCENDANT_CPU_PCT):
        return "working"
    return "waiting"


def _claude_project_dir_max_transcript_unix(proj: Path) -> float | None:
    """Latest JSONL transcript timestamp under a Claude Code project dir."""
    max_u: float | None = None
    candidates: list[Path] = []
    conv = proj / "conversation.jsonl"
    try:
        if conv.is_file():
            candidates.append(conv)
        for p in proj.rglob("agent-*.jsonl"):
            try:
                if p.is_file():
                    candidates.append(p)
            except OSError:
                pass
    except OSError:
        pass
    for p in candidates[:64]:
        r = jsonl_tail_timestamp_result(p, use_payload_timestamp=False)
        if r.max_unix is not None and (max_u is None or r.max_unix > max_u):
            max_u = r.max_unix
    return max_u


def _claude_tty_session_status(pid: int, cwd: str | None) -> str | None:
    if cwd is None:
        return None
    proj = _claude_project_dir_for_cwd(cwd)
    if proj is None:
        return None
    last = _claude_project_dir_max_transcript_unix(proj)
    return _working_or_waiting_from_signals(pid, last)


def _claude_desktop_session_status(session_file: Path) -> str | None:
    """Desktop session without a reliable agent PID — transcript age only."""
    r = jsonl_tail_timestamp_result(session_file, use_payload_timestamp=False)
    last = r.max_unix
    if last is None:
        return None
    now = time.time()
    if now - last < _STATUS_RECENCY_SEC:
        return "working"
    return "waiting"


def _codex_exec_indicates_complete(path: Path) -> bool:
    """True if rollout JSONL ends with a ``task_complete`` event (``codex exec``)."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size == 0:
        return False
    tail_bytes = min(size, 98304)
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - tail_bytes))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    lines = raw.splitlines()
    if size > tail_bytes and lines:
        lines = lines[1:]
    for line in reversed(lines[-400:]):
        line = line.strip()
        if not line or "task_complete" not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload")
        if isinstance(payload, dict) and payload.get("type") == "task_complete":
            return True
    return False


def _codex_app_file_session_status(session_file: Path) -> str | None:
    """Codex Desktop rollout file — transcript age only (no agent PID for CPU)."""
    r = jsonl_tail_timestamp_result(session_file, use_payload_timestamp=True)
    last = r.max_unix
    if last is None:
        return None
    now = time.time()
    if now - last < _STATUS_RECENCY_SEC:
        return "working"
    return "waiting"


def _codex_cli_session_status(pid: int, cwd: str | None, argv: str) -> str | None:
    data = _ensure_codex_tick_data()
    nc = _normalise_cwd(cwd)
    path = data.cli_rollout_by_cwd.get(nc) if nc else None
    last: float | None = None
    if path is not None:
        r = jsonl_tail_timestamp_result(path, use_payload_timestamp=True)
        last = r.max_unix
    if " exec" in argv and path is not None and _codex_exec_indicates_complete(path):
        return "done"
    return _working_or_waiting_from_signals(pid, last)


def _cursor_workspace_coarse_status_unix(project_dir: Path) -> float | None:
    """Best-effort activity instant: agent jsonl timestamps and terminal file mtimes."""
    max_u: float | None = None
    tx = project_dir / "agent-transcripts"
    if tx.is_dir():
        try:
            for p in tx.rglob("*.jsonl"):
                try:
                    if not p.is_file():
                        continue
                    r = jsonl_tail_timestamp_result(p, use_payload_timestamp=False)
                    if r.max_unix is not None and (max_u is None or r.max_unix > max_u):
                        max_u = r.max_unix
                except OSError:
                    pass
        except OSError:
            pass
    terminals = project_dir / "terminals"
    if terminals.is_dir():
        try:
            with os.scandir(terminals) as it:
                for entry in it:
                    if not entry.name.endswith(".txt"):
                        continue
                    try:
                        mt = entry.stat(follow_symlinks=False).st_mtime
                        if max_u is None or mt > max_u:
                            max_u = mt
                    except OSError:
                        pass
        except OSError:
            pass
    return max_u


def _cursor_coarse_status(project_dir: Path) -> str | None:
    last = _cursor_workspace_coarse_status_unix(project_dir)
    if last is None:
        return None
    if time.time() - last < _STATUS_RECENCY_SEC:
        return "working"
    return "waiting"


@lru_cache(maxsize=512)
def _synthetic_pid_str(path_str: str) -> int:
    h = int(hashlib.blake2b(path_str.encode(), digest_size=4).hexdigest(), 16)
    return 100_000 + (h % 900_000)


def _synthetic_pid(path: Path) -> int:
    return _synthetic_pid_str(str(path))


def _cursor_project_cwd(project_dir: Path) -> str | None:
    """Read the cwd from the most recent terminal file in a Cursor project dir."""
    terminals_dir = project_dir / "terminals"
    if not terminals_dir.exists():
        return None
    try:
        files = sorted(terminals_dir.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    max_lines = 48
    for f in files:
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines()):
                if i >= max_lines:
                    break
                if line.startswith("cwd:"):
                    return line[4:].strip().strip('"')
        except OSError:
            pass
    return None


def _cursor_project_workspace_cwd(project_dir: Path) -> str | None:
    """Resolve workspace path: terminal metadata first, then ~/.cursor/projects/<slug> inverse."""
    cwd = _cursor_project_cwd(project_dir)
    if cwd is not None:
        return cwd
    name = project_dir.name
    if not name or "-" not in name:
        return None
    body = name[1:] if name.startswith("-") else name
    if not body or body.count("-") < 2:
        # Avoid ambiguous short slugs (e.g. ``Users-me`` → ``/Users/me``) that are not
        # Cursor's typical multi-segment workspace encoding.
        return None
    candidate = "/" + body.replace("-", "/")
    try:
        resolved_proj = project_dir.resolve()
    except OSError:
        return None
    hit = _cursor_project_dir_for_cwd(candidate)
    if hit is None:
        return None
    try:
        if hit.resolve() == resolved_proj:
            return candidate
    except OSError:
        pass
    return None


def _cursor_project_dir_for_cwd(cwd: str) -> Path | None:
    """Resolve ~/.cursor/projects/<encoded> for a workspace path, if it exists."""
    if not _CURSOR_PROJECTS_DIR.exists():
        return None
    tail = cwd.rstrip("/")
    if not tail or tail == "/":
        return None
    slug = tail.lstrip("/").replace("/", "-")
    for name in (slug, f"-{slug}"):
        p = _CURSOR_PROJECTS_DIR / name
        if p.is_dir():
            return p
    return None


def _latest_mtime_under(root: Path) -> float:
    """Newest mtime across top-level entries (files *and* sub-directories).

    Cursor project dirs (``~/.cursor/projects/<encoded>/``) only contain
    sub-directories — ``terminals/``, ``agent-transcripts/``, ``mcps/``, etc. —
    where the actual workspace activity lives. A directory's mtime updates
    when its direct children are created/deleted/renamed, so stat'ing the
    top-level entries (without recursing) is enough to catch active editor
    sessions while remaining O(top-level-fanout).

    Empty/missing tree → ``0.0`` so callers can treat "no signal" uniformly.
    """
    key = str(root)
    if key in _mtime_cache:
        return _mtime_cache[key]
    latest = 0.0
    saw_entry = False
    try:
        with os.scandir(root) as it:
            for entry in it:
                try:
                    latest = max(latest, entry.stat(follow_symlinks=False).st_mtime)
                    saw_entry = True
                except OSError:
                    pass
    except OSError:
        _mtime_cache[key] = 0.0
        return 0.0
    if not saw_entry:
        latest = 0.0
    _mtime_cache[key] = latest
    return latest


def _claude_project_dir_for_cwd(cwd: str) -> Path | None:
    """Resolve ~/.claude/projects/<encoded> for a filesystem cwd, if it exists."""
    if not _CLAUDE_PROJECTS_DIR.exists():
        return None
    tail = cwd.rstrip("/")
    if not tail:
        return None
    slug = tail.lstrip("/").replace("/", "-")
    candidates = (
        _CLAUDE_PROJECTS_DIR / f"-{slug}",
        _CLAUDE_PROJECTS_DIR / slug,
    )
    for p in candidates:
        if p.is_dir():
            return p
    return None


def _resolve_cwd_cached(pid: int) -> str | None:
    """Resolve cwd for pid, cached for the current tick (cache cleared each tick)."""
    if pid not in _cwd_cache:
        _cwd_cache[pid] = resolve_cwd(pid)
    return _cwd_cache[pid]


def _jsonl_files_with_mtime(root: Path, cutoff: float) -> list[tuple[Path, float]]:
    """Recursively collect (path, mtime) for .jsonl files with mtime >= cutoff."""
    result: list[tuple[Path, float]] = []
    try:
        for entry in os.scandir(root):
            if entry.is_dir(follow_symlinks=False):
                result.extend(_jsonl_files_with_mtime(Path(entry.path), cutoff))
            elif entry.name.endswith(".jsonl"):
                try:
                    mtime = entry.stat().st_mtime
                    if mtime >= cutoff:
                        result.append((Path(entry.path), mtime))
                except OSError:
                    pass
    except OSError:
        pass
    return result


def _ensure_codex_tick_data() -> CodexTickData:
    global _codex_tick_data
    if _codex_tick_data is not None:
        return _codex_tick_data
    now = time.time()
    active_cutoff = now - _ACTIVITY_WINDOW_SEC
    scan_cutoff = now - 3600
    cli_cwds: set[str] = set()
    cli_rollout_by_cwd: dict[str, Path] = {}
    desktop_rows: list[tuple[Path, float, str | None]] = []
    if _CODEX_SESSIONS_DIR.exists():
        for path, mtime in _jsonl_files_with_mtime(_CODEX_SESSIONS_DIR, scan_cutoff):
            originator, meta_cwd = _codex_session_meta(path)
            if originator == "Codex Desktop":
                if mtime >= active_cutoff:
                    desktop_rows.append((path, mtime, meta_cwd))
            elif mtime >= active_cutoff:
                if not jsonl_transcript_recent(
                    path,
                    active_cutoff,
                    use_payload_timestamp=True,
                ):
                    continue
                nc = _normalise_cwd(meta_cwd)
                if nc:
                    cli_cwds.add(nc)
                    prev = cli_rollout_by_cwd.get(nc)
                    if prev is None:
                        cli_rollout_by_cwd[nc] = path
                    else:
                        try:
                            if mtime >= prev.stat().st_mtime:
                                cli_rollout_by_cwd[nc] = path
                        except OSError:
                            cli_rollout_by_cwd[nc] = path
    desktop_rows.sort(key=lambda x: x[1], reverse=True)
    _codex_tick_data = CodexTickData(
        cli_active_cwds=frozenset(cli_cwds),
        cli_rollout_by_cwd=cli_rollout_by_cwd,
        desktop_rows=desktop_rows,
    )
    return _codex_tick_data


def _claude_project_agent_transcripts_recent(proj: Path, cutoff: float) -> bool:
    """True if agent jsonl tails lack timestamps (fallback) or max timestamp >= cutoff."""
    had_any_parseable = False
    max_u: float | None = None
    candidates: list[Path] = []
    try:
        for p in proj.rglob("agent-*.jsonl"):
            try:
                if p.stat().st_mtime >= cutoff:
                    candidates.append(p)
            except OSError:
                pass
    except OSError:
        return True
    try:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return True
    for p in candidates[:48]:
        r = jsonl_tail_timestamp_result(p, use_payload_timestamp=False)
        if r.had_parseable:
            had_any_parseable = True
            if r.max_unix is not None and (max_u is None or r.max_unix > max_u):
                max_u = r.max_unix
    if not had_any_parseable:
        return True
    return max_u is not None and max_u >= cutoff


def claude_cli_session_is_active(pid: int) -> bool:
    """Return True when Claude CLI shows recent project or CPU activity."""
    if cpu_is_active(pid):
        return True
    cwd = _resolve_cwd_cached(pid)
    if cwd is None:
        return False
    proj = _claude_project_dir_for_cwd(cwd)
    if proj is None:
        return False
    cutoff = time.time() - _ACTIVITY_WINDOW_SEC
    latest = _latest_mtime_under(proj)
    if latest <= 0.0 or latest < cutoff:
        return False
    return _claude_project_agent_transcripts_recent(proj, cutoff)


def _claude_session_meta(session_file: Path) -> tuple[str | None, str | None]:
    """Return (entrypoint, cwd) from a Claude session jsonl without a full-file scan."""
    entrypoint: str | None = None
    cwd: str | None = None
    prefix_bytes = 65536
    tail_bytes = 32768
    try:
        size = session_file.stat().st_size
    except OSError:
        return None, None
    try:
        with open(session_file, "rb") as f:
            head = f.read(prefix_bytes).decode("utf-8", errors="replace")
        for line in head.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            line_entrypoint = payload.get("entrypoint")
            line_cwd = payload.get("cwd")
            if isinstance(line_entrypoint, str):
                entrypoint = line_entrypoint
            if isinstance(line_cwd, str):
                cwd = line_cwd
        if entrypoint is not None and cwd is not None:
            return entrypoint, cwd
        if size > prefix_bytes:
            with open(session_file, "rb") as f:
                f.seek(max(0, size - tail_bytes))
                tail = f.read().decode("utf-8", errors="replace")
            for line in reversed(tail.splitlines()):
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                line_entrypoint = payload.get("entrypoint")
                line_cwd = payload.get("cwd")
                if isinstance(line_entrypoint, str):
                    entrypoint = line_entrypoint
                if isinstance(line_cwd, str):
                    cwd = line_cwd
                if entrypoint is not None and cwd is not None:
                    break
    except OSError:
        return None, None
    return entrypoint, cwd


def cpu_is_active(pid: int) -> bool:
    """Return True if process CPU% exceeds the activity threshold."""
    return _cpu_percent(pid) > _CPU_ACTIVITY_THRESHOLD


def cursor_agent_session_is_active(project_dir: Path) -> bool:
    """Return True if any Cursor agent transcript was modified within the activity window."""
    transcripts_dir = project_dir / "agent-transcripts"
    if not transcripts_dir.exists():
        return False
    cutoff = time.time() - _ACTIVITY_WINDOW_SEC
    # Use file mtimes only: the agent-transcripts/ directory mtime often stays stale while
    # Cursor appends to existing jsonl (many filesystems do not bump the parent on writes).
    for f in transcripts_dir.rglob("*.jsonl"):
        try:
            if f.stat().st_mtime > cutoff:
                return True
        except OSError:
            pass
    return False


def codex_app_session_is_active(session_file: Path) -> bool:
    """Return True if the Codex Desktop session file is active by mtime and transcript time."""
    cutoff = time.time() - _ACTIVITY_WINDOW_SEC
    try:
        if session_file.stat().st_mtime <= cutoff:
            return False
    except OSError:
        return False
    return jsonl_transcript_recent(
        session_file,
        cutoff,
        use_payload_timestamp=True,
    )


def _codex_session_meta(session_file: Path) -> tuple[str | None, str | None]:
    """Return (originator, cwd) from the first line of a Codex session jsonl."""
    try:
        with open(session_file, errors="replace") as f:
            first = f.readline()
        d = json.loads(first)
        payload = d.get("payload", {})
        return payload.get("originator"), payload.get("cwd")
    except (OSError, json.JSONDecodeError, KeyError):
        return None, None


def _normalise_cwd(cwd: str | None) -> str | None:
    if cwd is None:
        return None
    t = cwd.rstrip("/")
    return t or None


def codex_cli_session_is_active(pid: int) -> bool:
    """Return True when Codex CLI shows recent session-file or CPU activity."""
    if cpu_is_active(pid):
        return True
    cwd = _normalise_cwd(_resolve_cwd_cached(pid))
    if cwd is None:
        return False
    if not _CODEX_SESSIONS_DIR.exists():
        return False
    data = _ensure_codex_tick_data()
    return cwd in data.cli_active_cwds


def _dedupe_codex_cli_sessions(sessions: list[Session]) -> list[Session]:
    """Collapse Codex wrapper/child process pairs into one session row."""
    if len(sessions) < 2:
        return sessions

    by_pid = {s.pid: s for s in sessions}
    drop: set[int] = set()
    for s in sessions:
        parent = _ppid(s.pid)
        if parent is None or parent not in by_pid:
            continue
        parent_session = by_pid[parent]
        if _normalise_cwd(parent_session.cwd) != _normalise_cwd(s.cwd):
            continue
        drop.add(parent)
    return [s for s in sessions if s.pid not in drop]


def _claude_pgrep_all() -> list[int]:
    """Return all Claude CLI PIDs in one combined pgrep call."""
    return _pgrep(_CLAUDE_CLI_COMBINED_PATTERN)


def list_claude_app_sessions() -> list[Session]:
    """Detect active Claude desktop sessions via recent session files."""
    if not _CLAUDE_PROJECTS_DIR.exists():
        return []
    cutoff = time.time() - _ACTIVITY_WINDOW_SEC
    candidates = sorted(
        _jsonl_files_with_mtime(_CLAUDE_PROJECTS_DIR, cutoff),
        key=lambda x: x[1],
        reverse=True,
    )
    sessions: list[Session] = []
    for session_file, _mtime in candidates:
        entrypoint, cwd = _claude_session_meta(session_file)
        if entrypoint != "claude-desktop":
            continue
        if cwd is None:
            continue
        if not jsonl_transcript_recent(
            session_file,
            cutoff,
            use_payload_timestamp=False,
        ):
            continue
        sessions.append(
            Session(
                tool="claude",
                pid=_synthetic_pid(session_file),
                cwd=cwd,
                status=_claude_desktop_session_status(session_file),
                transcript_path=session_file,
            ),
        )
    return sessions


def _merge_claude_tty_with_desktop(
    tty_sessions: list[Session],
    app_sessions: list[Session],
) -> list[Session]:
    """Drop TTY claude rows when a desktop session already covers the same cwd."""
    desktop_norm: set[str] = set()
    for s in app_sessions:
        n = _normalise_cwd(s.cwd)
        if n is not None:
            desktop_norm.add(n)
    if not tty_sessions:
        return list(app_sessions)
    pids = [s.pid for s in tty_sessions]
    cwds = resolve_cwds_batch(pids)
    kept_tty: list[Session] = []
    for s in tty_sessions:
        cwd = cwds.get(s.pid)
        n = _normalise_cwd(cwd)
        if n is not None and n in desktop_norm:
            continue
        st = _claude_tty_session_status(s.pid, cwd)
        kept_tty.append(Session(tool="claude", pid=s.pid, cwd=cwd, status=st))
    return list(app_sessions) + kept_tty


def list_claude_sessions() -> list[Session]:
    """Detect active Claude Code terminal and desktop sessions."""
    tty_sessions: list[Session] = []
    for pid in _claude_pgrep_all():
        if not _has_tty(pid):
            continue
        if is_descendant_of(pid, ["ralph", "cron"]):
            continue
        if not claude_cli_session_is_active(pid):
            continue
        tty_sessions.append(Session(tool="claude", pid=pid))

    return _merge_claude_tty_with_desktop(tty_sessions, list_claude_app_sessions())


def _cursor_editor_workspace_is_active(project_dir: Path, cutoff: float) -> bool:
    """True when agent transcripts or integrated-terminal snapshots look recently used.

    Avoids treating ``mcps/``, ``assets/``, etc. (often refreshed app-wide) as workspace
    activity — those updates bump top-level directory mtimes without local editing.
    """
    if cursor_agent_session_is_active(project_dir):
        return True
    terminals_dir = project_dir / "terminals"
    if not terminals_dir.is_dir():
        return False
    try:
        with os.scandir(terminals_dir) as it:
            for entry in it:
                if not entry.name.endswith(".txt"):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                        return True
                except OSError:
                    pass
    except OSError:
        pass
    return False


def list_cursor_editor_windows() -> list[Session]:
    """Detect open Cursor editor workspaces via the per-project state dirs.

    Cursor's renderer processes (``Cursor Helper``) report ``cwd=/`` via lsof
    on current macOS builds, so we can't tie a Helper PID back to a workspace.
    Instead we gate on Cursor actually running (any Helper PID present), then
    scan ``~/.cursor/projects/<encoded>/`` — the same source
    :func:`list_cursor_agent_sessions` reads — and emit a session for every
    project dir with recent **agent** or **integrated-terminal** activity that
    exposes a workspace cwd.
    """
    if not _pgrep("Cursor Helper"):
        return []
    if not _CURSOR_PROJECTS_DIR.exists():
        return []
    cutoff = time.time() - _ACTIVITY_WINDOW_SEC
    sessions: list[Session] = []
    seen_norm: set[str] = set()
    for project_dir in _CURSOR_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        if not _cursor_editor_workspace_is_active(project_dir, cutoff):
            continue
        cwd = _cursor_project_workspace_cwd(project_dir)
        if cwd is None:
            continue
        norm = _normalise_cwd(cwd)
        if norm is None or norm in seen_norm:
            continue
        seen_norm.add(norm)
        sessions.append(
            Session(
                tool="cursor_editor",
                pid=_synthetic_pid(project_dir),
                cwd=cwd,
                status=_cursor_coarse_status(project_dir),
            ),
        )
    return sessions


def list_cursor_agent_sessions() -> list[Session]:
    """Detect active Cursor background agent sessions via transcript mtimes."""
    if not _CURSOR_PROJECTS_DIR.exists():
        return []
    sessions = []
    for project_dir in _CURSOR_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        transcripts_dir = project_dir / "agent-transcripts"
        if not transcripts_dir.exists():
            continue
        if not cursor_agent_session_is_active(project_dir):
            continue
        cwd = _cursor_project_workspace_cwd(project_dir)
        if cwd is None:
            continue
        pid = _synthetic_pid(project_dir)
        sessions.append(
            Session(
                tool="cursor_agent",
                pid=pid,
                cwd=cwd,
                status=_cursor_coarse_status(project_dir),
            ),
        )
    return sessions


def _merge_cursor_editor_and_agent(
    editor: list[Session],
    agent: list[Session],
) -> list[Session]:
    """At most one Cursor session per workspace; prefer cursor_agent when both qualify."""
    by_norm: dict[str, Session] = {}
    for s in agent:
        n = _normalise_cwd(s.cwd)
        if n is not None:
            by_norm[n] = s
    for s in editor:
        n = _normalise_cwd(s.cwd)
        if n is None or n in by_norm:
            continue
        by_norm[n] = s
    return list(by_norm.values())


def list_codex_sessions() -> list[Session]:
    """Detect interactive Codex CLI sessions, excluding daemon."""
    sessions = []
    for pid in _pgrep(r"codex( |$)"):
        argv = _argv(pid)
        if "codex-companion" in argv:
            continue
        parent = _ppid(pid)
        if parent == 1:
            continue
        if not _has_tty(pid):
            continue
        if is_descendant_of(pid, ["ralph", "cron"]):
            continue
        if not codex_cli_session_is_active(pid):
            continue
        cwd = _resolve_cwd_cached(pid)
        sessions.append(
            Session(
                tool="codex",
                pid=pid,
                cwd=cwd,
                status=_codex_cli_session_status(pid, cwd, argv),
            ),
        )
    return _dedupe_codex_cli_sessions(sessions)


def list_codex_app_sessions() -> list[Session]:
    """Detect active Codex Desktop app sessions via recent session files."""
    if not _CODEX_SESSIONS_DIR.exists():
        return []
    data = _ensure_codex_tick_data()
    sessions = []
    seen_keys: set[str] = set()
    for path, _mtime, cwd in data.desktop_rows:
        norm_cwd = _normalise_cwd(cwd)
        dedupe_key = norm_cwd if norm_cwd is not None else str(path.resolve())
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        if not codex_app_session_is_active(path):
            continue
        pid = _synthetic_pid(path)
        st = _codex_app_file_session_status(path)
        sessions.append(
            Session(tool="codex", pid=pid, cwd=cwd, status=st, transcript_path=path),
        )
    return sessions


def _clear_session_metrics(s: Session) -> None:
    s.model = None
    s.input_tokens = None
    s.output_tokens = None
    s.cache_read = None
    s.cache_create = None


def _enrich_session_metrics(sessions: list[Session], config: Config) -> None:
    if not config.session_metrics:
        return
    data = _ensure_codex_tick_data()
    for s in sessions:
        if s.tool in ("cursor_editor", "cursor_agent"):
            continue
        if config.is_redacted(s.cwd) or s.project == "redacted":
            _clear_session_metrics(s)
            continue
        snap = None
        if s.tool == "claude":
            if s.transcript_path is not None:
                snap = parse_claude_jsonl_tail(s.transcript_path)
            elif s.cwd is not None:
                proj = _claude_project_dir_for_cwd(s.cwd)
                if proj is not None:
                    snap = parse_claude_project_dir(proj)
        elif s.tool == "codex":
            if s.transcript_path is not None:
                snap = parse_codex_rollout_tail(s.transcript_path)
            elif s.cwd is not None:
                nc = _normalise_cwd(s.cwd)
                path = data.cli_rollout_by_cwd.get(nc) if nc else None
                if path is not None:
                    snap = parse_codex_rollout_tail(path)
        if snap is None:
            continue
        s.model = snap.model
        s.input_tokens = snap.input_tokens
        s.output_tokens = snap.output_tokens
        s.cache_read = snap.cache_read
        s.cache_create = snap.cache_create


def list_all_sessions() -> list[Session]:
    """Return all detected active sessions."""
    cursor_ed = list_cursor_editor_windows()
    cursor_ag = list_cursor_agent_sessions()
    return (
        list_claude_sessions()
        + _merge_cursor_editor_and_agent(cursor_ed, cursor_ag)
        + list_codex_sessions()
        + list_codex_app_sessions()
    )


class Sampler:
    """Sample sessions from the OS; raw result in ``_curr`` after ``tick()``."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._curr: list[Session] | None = None
        self.tick_id: int = 0

    @staticmethod
    def raw_session_keys(sessions: list[Session]) -> frozenset[tuple[str, int]]:
        return frozenset((s.tool, s.pid) for s in sessions)

    def tick(self) -> None:
        """Sample current sessions from the OS."""
        self.tick_id += 1
        _begin_tick()
        raw = list_all_sessions()
        # Synthetic session PIDs are fake; skip lsof batch for them.
        pids_needing = sorted({s.pid for s in raw if s.cwd is None and s.pid < 100_000})
        if pids_needing:
            for pid, cwd in resolve_cwds_batch(pids_needing).items():
                _cwd_cache[pid] = cwd
        for s in raw:
            if s.cwd is None:
                s.cwd = _cwd_cache.get(s.pid)
            if s.project is None and s.cwd is not None:
                s.project = project_label(s.cwd, self._config)
        _enrich_session_metrics(raw, self._config)
        self._curr = raw

    def raw_sessions(self) -> list[Session]:
        """Return the last raw sample (copy)."""
        if self._curr is None:
            return []
        return list(self._curr)


def stable_sessions_from_keys(
    sessions: list[Session],
    persisted_prev: frozenset[tuple[str, int]],
) -> list[Session]:
    """Intersection of persisted raw keys with current sessions (debounced stable set)."""
    stable = persisted_prev & Sampler.raw_session_keys(sessions)
    return [s for s in sessions if (s.tool, s.pid) in stable]
