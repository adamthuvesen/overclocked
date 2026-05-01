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
from overclocked.transcript_metrics import (
    parse_claude_jsonl_tail,
    parse_claude_project_dir,
    parse_codex_rollout_tail,
)
from overclocked.transcript_time import jsonl_tail_timestamp_result, jsonl_transcript_recent


@dataclass
class Session:
    tool: str  # 'claude' | 'cursor_editor' | 'cursor_agent' | 'codex'
    pid: int
    cwd: str | None = None
    project: str | None = None
    #: True when ``pid`` is derived from a path hash (not an OS PID — do not use lsof).
    synthetic: bool = False
    #: ``working`` | ``waiting`` | ``done`` (abtop-style); ``None`` when unknown
    status: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read: int | None = None
    cache_create: int | None = None
    #: Desktop / file-backed session jsonl for metrics (Claude desktop, Codex desktop)
    transcript_path: Path | None = None
    #: Claude Code session UUID; used to link subagents to their parent.
    session_id: str | None = None
    #: True when this row represents a Task-tool subagent of another session.
    is_subagent: bool = False
    #: Parent ``session_id`` when ``is_subagent`` is True.
    parent_session_id: str | None = None
    #: Claude subagent agentId (``agent-<id>.jsonl`` filename body).
    agent_id: str | None = None


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
    #: rollout path → meta.id, populated for every fresh rollout we read.
    rollout_id_by_path: dict[Path, str]
    #: parent rollout id → live subagent rows.
    subagent_rows_by_parent: dict[str, list[CodexSubagentRow]]


@dataclass(frozen=True)
class CodexSubagentRow:
    child_id: str
    mtime: float
    cwd: str | None
    agent_nickname: str | None
    transcript_path: Path


# Process table for current tick (None → ps axo failed, treat as no processes)
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
# Subagents append to their jsonl on every tool turn, so a 30s gap is a
# strong signal the subagent has finished — drop the row instead of letting
# it linger for the full 5-min activity window.
_SUBAGENT_LIVENESS_SEC = _STATUS_RECENCY_SEC
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
    if not _ps_table or pid not in _ps_table:
        return False
    tty = _ps_table[pid].tty.strip()
    return bool(tty) and tty != "??"


def _argv(pid: int) -> str:
    if not _ps_table or pid not in _ps_table:
        return ""
    return _ps_table[pid].command


def _ppid(pid: int) -> int | None:
    if not _ps_table or pid not in _ps_table:
        return None
    return _ps_table[pid].ppid


def _cpu_percent(pid: int) -> float:
    if not _ps_table or pid not in _ps_table:
        return 0.0
    return _ps_table[pid].pcpu


def is_descendant_of(pid: int, names: list[str]) -> bool:
    """Return True if any ancestor process has a name matching names."""
    if not _ps_table:
        return False
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


def _cursor_project_dir_matches_cwd(project_dir: Path, cwd: str) -> bool:
    hit = _cursor_project_dir_for_cwd(cwd)
    if hit is None:
        return False
    try:
        return hit.resolve() == project_dir.resolve()
    except OSError:
        return False


def _cursor_agent_candidate_rank(project_dir: Path, cwd: str) -> tuple[int, int, float, str]:
    """Rank duplicate Cursor session mirrors; real workspace dirs beat empty-window."""
    exact_workspace = 1 if _cursor_project_dir_matches_cwd(project_dir, cwd) else 0
    named_workspace = 0 if project_dir.name == "empty-window" else 1
    last_activity = _cursor_workspace_coarse_status_unix(project_dir) or 0.0
    return (exact_workspace, named_workspace, last_activity, project_dir.name)


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
    """Resolve ~/.claude/projects/<encoded> for a filesystem cwd, if it exists.

    Claude Code encodes the cwd by replacing ``/``, ``.``, and ``_`` with ``-``
    (see :func:`_encode_cwd_for_claude_projects`) — so a cwd like
    ``/Users/me/dev/x/.claude/wt`` lands at ``-Users-me-dev-x--claude-wt``.
    The simpler ``/``-only encoding is kept as a fallback for paths without
    ``.``/``_`` to stay forward-compatible with any older layout on disk.
    """
    if not _CLAUDE_PROJECTS_DIR.exists():
        return None
    tail = cwd.rstrip("/")
    if not tail:
        return None
    full = _encode_cwd_for_claude_projects(tail)
    slug = tail.lstrip("/").replace("/", "-")
    candidates = (
        _CLAUDE_PROJECTS_DIR / full,
        _CLAUDE_PROJECTS_DIR / f"-{slug}",
        _CLAUDE_PROJECTS_DIR / slug,
    )
    for p in candidates:
        if p.is_dir():
            return p
    return None


def _claude_config_base() -> Path:
    """Claude Code config root (``~/.claude`` or ``CLAUDE_CONFIG_DIR``)."""
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".claude"


def _encode_cwd_for_claude_projects(cwd: str) -> str:
    """Match Claude Code project dir naming (same as abtop ``encode_cwd_path``)."""
    return "".join("-" if c in "/_." else c for c in cwd)


def _is_symlink(path: Path) -> bool:
    try:
        return path.is_symlink()
    except OSError:
        return True


def _find_claude_session_file_for_pid(sessions_dir: Path, pid: int) -> Path | None:
    """Resolve ``sessions/<pid>.json`` or any ``*.json`` whose ``pid`` field matches."""
    direct = sessions_dir / f"{pid}.json"
    if direct.is_file() and not _is_symlink(direct):
        return direct
    try:
        for path in sessions_dir.glob("*.json"):
            if _is_symlink(path):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("pid") == pid:
                return path
    except OSError:
        return None
    return None


def _claude_session_meta_from_disk(path: Path) -> tuple[str | None, str | None]:
    """Return ``(session_id, cwd)`` from a Claude Code ``sessions/*.json`` file."""
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    sid = data.get("sessionId") or data.get("session_id")
    cwd = data.get("cwd")
    sid_s = sid.strip() if isinstance(sid, str) and sid.strip() else None
    cwd_s = cwd if isinstance(cwd, str) else None
    return sid_s, cwd_s


def _find_claude_transcript_jsonl_for_tty(pid: int, cwd: str) -> Path | None:
    """Resolve per-session transcript: ``projects/<encode(cwd)>/<sessionId>.jsonl``."""
    base = _claude_config_base()
    sessions_dir = base / "sessions"
    if not sessions_dir.is_dir():
        return None
    session_json = _find_claude_session_file_for_pid(sessions_dir, pid)
    if session_json is None:
        return None
    session_id, file_cwd = _claude_session_meta_from_disk(session_json)
    if not session_id:
        return None
    if file_cwd is not None:
        n_file = _normalise_cwd(file_cwd)
        n_sess = _normalise_cwd(cwd)
        if n_file is not None and n_sess is not None and n_file != n_sess:
            return None
    projects = base / "projects"
    if not projects.is_dir():
        return None
    enc = _encode_cwd_for_claude_projects(cwd)
    direct = projects / enc / f"{session_id}.jsonl"
    if direct.is_file() and not _is_symlink(direct):
        return direct
    try:
        for entry in projects.iterdir():
            if not entry.is_dir() or _is_symlink(entry):
                continue
            cand = entry / f"{session_id}.jsonl"
            if cand.is_file() and not _is_symlink(cand):
                return cand
    except OSError:
        pass
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
    scan_cutoff = now - 86400  # 24h — desktop rows rely on transcript for freshness
    subagent_cutoff = now - _SUBAGENT_LIVENESS_SEC
    cli_cwds: set[str] = set()
    cli_rollout_by_cwd: dict[str, Path] = {}
    desktop_rows: list[tuple[Path, float, str | None]] = []
    rollout_id_by_path: dict[Path, str] = {}
    subagent_rows_by_parent: dict[str, list[CodexSubagentRow]] = {}
    if _CODEX_SESSIONS_DIR.exists():
        for path, mtime in _jsonl_files_with_mtime(_CODEX_SESSIONS_DIR, scan_cutoff):
            meta = _codex_session_meta(path)
            if meta.id:
                rollout_id_by_path[path] = meta.id
            if meta.parent_thread_id is not None:
                # Subagent rollout — group under its parent if mtime is fresh.
                if mtime >= subagent_cutoff and meta.id is not None:
                    subagent_rows_by_parent.setdefault(meta.parent_thread_id, []).append(
                        CodexSubagentRow(
                            child_id=meta.id,
                            mtime=mtime,
                            cwd=meta.cwd,
                            agent_nickname=meta.agent_nickname,
                            transcript_path=path,
                        ),
                    )
                continue
            if _is_codex_file_backed_originator(meta.originator):
                desktop_rows.append((path, mtime, meta.cwd))
            elif mtime >= active_cutoff:
                if not jsonl_transcript_recent(
                    path,
                    active_cutoff,
                    use_payload_timestamp=True,
                ):
                    continue
                nc = _normalise_cwd(meta.cwd)
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
    for rows in subagent_rows_by_parent.values():
        rows.sort(key=lambda r: r.child_id)  # deterministic order
    _codex_tick_data = CodexTickData(
        cli_active_cwds=frozenset(cli_cwds),
        cli_rollout_by_cwd=cli_rollout_by_cwd,
        desktop_rows=desktop_rows,
        rollout_id_by_path=rollout_id_by_path,
        subagent_rows_by_parent=subagent_rows_by_parent,
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
    return _cursor_active_session_id(transcripts_dir, cutoff) is not None


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


_CODEX_META_SCAN_BYTES = 65536


_CODEX_FILE_BACKED_ORIGINATORS = frozenset({"codex desktop", "codex-tui", "codex_vscode"})


def _is_codex_file_backed_originator(originator: str | None) -> bool:
    """True for originators whose sessions are detected via the rollout file walk.

    Excludes ``codex_cli_rs`` (detected via pgrep instead), ``codex_exec``
    (one-shot), ``codex_sdk_ts`` (programmatic), and one-off variants.
    """
    if not isinstance(originator, str):
        return False
    return originator.strip().casefold() in _CODEX_FILE_BACKED_ORIGINATORS


@dataclass(frozen=True)
class CodexRolloutMeta:
    originator: str | None
    cwd: str | None
    id: str | None
    parent_thread_id: str | None  # set iff this rollout is a subagent
    agent_nickname: str | None


def _codex_session_meta(session_file: Path) -> CodexRolloutMeta:
    """Parse a Codex rollout's session_meta record from the file prefix.

    Reads the first ~64 KiB and walks records until a ``session_meta`` is
    found (it is line 1 in practice, but malformed leading lines are tolerated).
    On parse failure (torn writes, missing meta line) returns a record with all
    fields ``None`` — callers must not assume any field is set.
    """
    try:
        with open(session_file, errors="replace") as f:
            head = f.read(_CODEX_META_SCAN_BYTES)
    except OSError:
        return CodexRolloutMeta(None, None, None, None, None)
    for line in head.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(d, dict) or d.get("type") != "session_meta":
            continue
        payload = d.get("payload")
        if not isinstance(payload, dict):
            continue
        parent_id = None
        source = payload.get("source")
        if isinstance(source, dict):
            sub = source.get("subagent")
            if isinstance(sub, dict):
                ts = sub.get("thread_spawn")
                if isinstance(ts, dict):
                    parent_id = ts.get("parent_thread_id")
        return CodexRolloutMeta(
            originator=payload.get("originator"),
            cwd=payload.get("cwd"),
            id=payload.get("id"),
            parent_thread_id=parent_id,
            agent_nickname=payload.get("agent_nickname"),
        )
    return CodexRolloutMeta(None, None, None, None, None)


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


def _claude_session_id_from_jsonl_path(path: Path) -> str | None:
    """Extract a Claude session UUID from ``<sessionId>.jsonl``."""
    stem = path.stem
    return stem or None


def _list_live_subagents_for_parent(parent: Session) -> list[Session]:
    """Synthesise rows for live Task-tool subagents of ``parent``.

    Subagent transcripts live at
    ``~/.claude/projects/<encoded_cwd>/<parentSessionId>/subagents/agent-<agentId>.jsonl``.
    The directory name carries the parent linkage; the filename carries the
    agentId. A subagent is considered live iff its jsonl mtime is within
    ``_ACTIVITY_WINDOW_SEC`` (matching the threshold used to gate the parent).
    """
    if parent.cwd is None or parent.session_id is None:
        return []
    proj = _claude_project_dir_for_cwd(parent.cwd)
    if proj is None:
        return []
    sub_dir = proj / parent.session_id / "subagents"
    if not sub_dir.is_dir():
        return []
    cutoff = time.time() - _SUBAGENT_LIVENESS_SEC
    rows: list[tuple[Path, str, float]] = []
    try:
        with os.scandir(sub_dir) as it:
            for entry in it:
                name = entry.name
                if not name.startswith("agent-") or not name.endswith(".jsonl"):
                    continue
                try:
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                agent_id = name[len("agent-") : -len(".jsonl")]
                if not agent_id:
                    continue
                rows.append((Path(entry.path), agent_id, mtime))
    except OSError:
        return []
    rows.sort(key=lambda r: r[1])  # deterministic order by agent_id
    sessions: list[Session] = []
    now = time.time()
    for path, agent_id, mtime in rows:
        status = "working" if (now - mtime) < _STATUS_RECENCY_SEC else "waiting"
        sessions.append(
            Session(
                tool="claude",
                pid=_synthetic_pid_str(f"subagent:{agent_id}"),
                cwd=parent.cwd,
                project=parent.project,
                synthetic=True,
                status=status,
                transcript_path=path,
                is_subagent=True,
                parent_session_id=parent.session_id,
                agent_id=agent_id,
            ),
        )
    return sessions


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
        # Subagent transcripts live at <project>/<sessionId>/subagents/agent-*.jsonl
        # and inherit the parent's entrypoint/cwd, so they'd masquerade as their
        # own top-level desktop session. They're surfaced separately as children
        # of the parent row by ``_list_live_subagents_for_parent``.
        if session_file.parent.name == "subagents":
            continue
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
                synthetic=True,
                session_id=_claude_session_id_from_jsonl_path(session_file),
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
        sid: str | None = None
        if cwd is not None:
            tty_path = _find_claude_transcript_jsonl_for_tty(s.pid, cwd)
            if tty_path is not None:
                sid = _claude_session_id_from_jsonl_path(tty_path)
        kept_tty.append(Session(tool="claude", pid=s.pid, cwd=cwd, status=st, session_id=sid))
    return list(app_sessions) + kept_tty


def list_claude_sessions(config: Config | None = None) -> list[Session]:
    """Detect active Claude Code terminal and desktop sessions (plus live subagents)."""
    tty_sessions: list[Session] = []
    for pid in _claude_pgrep_all():
        if not _has_tty(pid):
            continue
        if is_descendant_of(pid, ["ralph", "cron"]):
            continue
        if not claude_cli_session_is_active(pid):
            continue
        tty_sessions.append(Session(tool="claude", pid=pid))

    parents = _merge_claude_tty_with_desktop(tty_sessions, list_claude_app_sessions())
    if config is not None and not config.show_subagents:
        return parents
    children: list[Session] = []
    for parent in parents:
        children.extend(_list_live_subagents_for_parent(parent))
    return parents + children


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
        if project_dir.name == "empty-window":
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
                synthetic=True,
            ),
        )
    return sessions


def _cursor_active_session_id(transcripts_dir: Path, cutoff: float) -> str | None:
    """Return the sessionId dir name of the most-recently-active transcript.

    Cursor stores transcripts at ``agent-transcripts/<sessionId>/<sessionId>.jsonl``
    (and ``…/subagents/<UUID>.jsonl``). Walks every jsonl under ``transcripts_dir``,
    keeps the freshest mtime above ``cutoff``, and returns the direct child of
    ``transcripts_dir`` containing it. Returns ``None`` if no jsonl is fresh.
    """
    best_mtime = cutoff
    best_session_id: str | None = None
    try:
        for f in transcripts_dir.rglob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
            except OSError:
                continue
            if mtime <= best_mtime:
                continue
            try:
                rel_parts = f.relative_to(transcripts_dir).parts
            except ValueError:
                continue
            if not rel_parts:
                continue
            best_mtime = mtime
            best_session_id = rel_parts[0]
    except OSError:
        return None
    return best_session_id


def _list_live_cursor_subagents_for_parent(parent: Session) -> list[Session]:
    """Synthesise rows for live Cursor subagents of ``parent``.

    Subagent transcripts live at
    ``~/.cursor/projects/<encoded_cwd>/agent-transcripts/<sessionId>/subagents/<UUID>.jsonl``.
    The directory name carries the parent linkage; the bare file stem is the agentId.
    A subagent is considered live iff its jsonl mtime is within
    ``_SUBAGENT_LIVENESS_SEC``.
    """
    if parent.cwd is None or parent.session_id is None:
        return []
    project_dir = _cursor_project_dir_for_cwd(parent.cwd)
    if project_dir is None:
        return []
    sub_dir = project_dir / "agent-transcripts" / parent.session_id / "subagents"
    if not sub_dir.is_dir():
        return []
    cutoff = time.time() - _SUBAGENT_LIVENESS_SEC
    rows: list[tuple[Path, str, float]] = []
    try:
        with os.scandir(sub_dir) as it:
            for entry in it:
                name = entry.name
                if not name.endswith(".jsonl"):
                    continue
                try:
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if mtime < cutoff:
                    continue
                agent_id = name[: -len(".jsonl")]
                if not agent_id:
                    continue
                rows.append((Path(entry.path), agent_id, mtime))
    except OSError:
        return []
    rows.sort(key=lambda r: r[1])  # deterministic order by agent_id
    sessions: list[Session] = []
    now = time.time()
    for path, agent_id, mtime in rows:
        status = "working" if (now - mtime) < _STATUS_RECENCY_SEC else "waiting"
        sessions.append(
            Session(
                tool="cursor_agent",
                pid=_synthetic_pid_str(f"cursor-subagent:{agent_id}"),
                cwd=parent.cwd,
                project=parent.project,
                synthetic=True,
                status=status,
                transcript_path=path,
                is_subagent=True,
                parent_session_id=parent.session_id,
                agent_id=agent_id,
            ),
        )
    return sessions


def list_cursor_agent_sessions(config: Config | None = None) -> list[Session]:
    """Detect active Cursor background agent sessions via transcript mtimes."""
    if not _CURSOR_PROJECTS_DIR.exists():
        return []
    candidates_by_session_id: dict[str, tuple[tuple[int, int, float, str], Session]] = {}
    activity_cutoff = time.time() - _ACTIVITY_WINDOW_SEC
    for project_dir in _CURSOR_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        transcripts_dir = project_dir / "agent-transcripts"
        if not transcripts_dir.exists():
            continue
        session_id = _cursor_active_session_id(transcripts_dir, activity_cutoff)
        if session_id is None:
            continue
        cwd = _cursor_project_workspace_cwd(project_dir)
        if cwd is None:
            continue
        pid = _synthetic_pid(project_dir)
        rank = _cursor_agent_candidate_rank(project_dir, cwd)
        session = Session(
            tool="cursor_agent",
            pid=pid,
            cwd=cwd,
            status=_cursor_coarse_status(project_dir),
            synthetic=True,
            session_id=session_id,
        )
        existing = candidates_by_session_id.get(session_id)
        if existing is None or rank > existing[0]:
            candidates_by_session_id[session_id] = (rank, session)

    sessions = [session for _rank, session in candidates_by_session_id.values()]

    if config is not None and not config.show_subagents:
        return sessions
    children: list[Session] = []
    for parent in sessions:
        children.extend(_list_live_cursor_subagents_for_parent(parent))
    return sessions + children


def list_cursor_agent_cli_sessions() -> list[Session]:
    """Detect interactive ``cursor-agent`` CLI sessions via pgrep + TTY."""
    sessions: list[Session] = []
    for pid in _pgrep(r"cursor-agent( |$)"):
        if not _has_tty(pid):
            continue
        if is_descendant_of(pid, ["ralph", "cron"]):
            continue
        cwd = _resolve_cwd_cached(pid)
        sessions.append(Session(tool="cursor_agent", pid=pid, cwd=cwd))
    return sessions


def _merge_cursor_editor_and_agent(
    editor: list[Session],
    agent: list[Session],
) -> list[Session]:
    """At most one Cursor parent per workspace; prefer cursor_agent when both qualify.

    Subagent rows (``is_subagent=True``) share their parent's cwd, so they bypass
    the cwd-dedupe and pass through unchanged.
    """
    by_norm: dict[str, Session] = {}
    subagents: list[Session] = []
    for s in agent:
        if s.is_subagent:
            subagents.append(s)
            continue
        n = _normalise_cwd(s.cwd)
        if n is not None:
            by_norm[n] = s
    for s in editor:
        n = _normalise_cwd(s.cwd)
        if n is None or n in by_norm:
            continue
        by_norm[n] = s
    return list(by_norm.values()) + subagents


def _list_live_codex_subagents_for_parents(parents: list[Session]) -> list[Session]:
    """Synthesise rows for live Codex subagents whose parent_thread_id matches one of ``parents``.

    Uses the tick-cached ``CodexTickData.subagent_rows_by_parent`` index — populated
    once per tick by ``_ensure_codex_tick_data``. A child rollout is "live" if its
    mtime is within ``_SUBAGENT_LIVENESS_SEC`` (already filtered during indexing).
    """
    if not _CODEX_SESSIONS_DIR.exists():
        return []
    data = _ensure_codex_tick_data()
    if not data.subagent_rows_by_parent:
        return []
    out: list[Session] = []
    now = time.time()
    for parent in parents:
        if parent.session_id is None:
            continue
        rows = data.subagent_rows_by_parent.get(parent.session_id)
        if not rows:
            continue
        for row in rows:
            status = "working" if (now - row.mtime) < _STATUS_RECENCY_SEC else "waiting"
            out.append(
                Session(
                    tool="codex",
                    pid=_synthetic_pid_str(f"codex-subagent:{row.child_id}"),
                    cwd=row.cwd or parent.cwd,
                    project=parent.project,
                    synthetic=True,
                    status=status,
                    transcript_path=row.transcript_path,
                    is_subagent=True,
                    parent_session_id=parent.session_id,
                    agent_id=row.child_id,
                ),
            )
    return out


def list_codex_sessions(config: Config | None = None) -> list[Session]:
    """Detect interactive Codex CLI sessions, excluding daemon."""
    parents: list[Session] = []
    data = _ensure_codex_tick_data() if _CODEX_SESSIONS_DIR.exists() else None
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
        session_id: str | None = None
        if data is not None:
            nc = _normalise_cwd(cwd)
            if nc is not None:
                rollout = data.cli_rollout_by_cwd.get(nc)
                if rollout is not None:
                    session_id = data.rollout_id_by_path.get(rollout)
        parents.append(
            Session(
                tool="codex",
                pid=pid,
                cwd=cwd,
                status=_codex_cli_session_status(pid, cwd, argv),
                session_id=session_id,
            ),
        )
    parents = _dedupe_codex_cli_sessions(parents)
    if config is not None and not config.show_subagents:
        return parents
    return parents + _list_live_codex_subagents_for_parents(parents)


def list_codex_app_sessions(config: Config | None = None) -> list[Session]:
    """Detect active file-backed Codex sessions (Desktop, TUI, IDE-embedded)."""
    if not _CODEX_SESSIONS_DIR.exists():
        return []
    data = _ensure_codex_tick_data()
    parents: list[Session] = []
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
        parents.append(
            Session(
                tool="codex",
                pid=pid,
                cwd=cwd,
                status=st,
                transcript_path=path,
                synthetic=True,
                session_id=data.rollout_id_by_path.get(path),
            ),
        )
    if config is not None and not config.show_subagents:
        return parents
    return parents + _list_live_codex_subagents_for_parents(parents)


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
        if s.is_subagent:
            # Subagent token usage rolls up to the parent transcript; surfacing
            # per-subagent metrics is a separate, harder change. Skip for now.
            continue
        if config.is_redacted(s.cwd) or s.project == "redacted":
            _clear_session_metrics(s)
            continue
        snap = None
        if s.tool == "claude":
            if s.transcript_path is not None:
                snap = parse_claude_jsonl_tail(s.transcript_path)
            elif s.cwd is not None:
                tty_path = _find_claude_transcript_jsonl_for_tty(s.pid, s.cwd)
                if tty_path is not None:
                    snap = parse_claude_jsonl_tail(tty_path)
                else:
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


def list_all_sessions(config: Config | None = None) -> list[Session]:
    """Return all detected active sessions."""
    cursor_ed = list_cursor_editor_windows()
    cursor_ag = list_cursor_agent_sessions(config) + list_cursor_agent_cli_sessions()
    return (
        list_claude_sessions(config)
        + _merge_cursor_editor_and_agent(cursor_ed, cursor_ag)
        + list_codex_sessions(config)
        + list_codex_app_sessions(config)
    )


def raw_session_keys(sessions: list[Session]) -> frozenset[tuple[str, int]]:
    return frozenset((s.tool, s.pid) for s in sessions)


def tick(config: Config) -> list[Session]:
    """Sample current sessions from the OS and return the enriched raw list."""
    _begin_tick()
    raw = list_all_sessions(config)
    # Synthetic session PIDs are fake; skip lsof batch for them.
    pids_needing = sorted({s.pid for s in raw if s.cwd is None and not s.synthetic})
    if pids_needing:
        for pid, cwd in resolve_cwds_batch(pids_needing).items():
            _cwd_cache[pid] = cwd
    for s in raw:
        if s.cwd is None:
            s.cwd = _cwd_cache.get(s.pid)
        if s.project is None and s.cwd is not None:
            s.project = project_label(s.cwd, config)
    _enrich_session_metrics(raw, config)
    return raw


def stable_sessions_from_keys(
    sessions: list[Session],
    persisted_prev: frozenset[tuple[str, int]],
) -> list[Session]:
    """Intersection of persisted raw keys with current sessions (debounced stable set)."""
    stable = persisted_prev & raw_session_keys(sessions)
    return [s for s in sessions if (s.tool, s.pid) in stable]
