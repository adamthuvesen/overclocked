"""SwiftBar-formatted rendering of session state."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from overclocked.aggregates import TodayHistoryContext
from overclocked.copy import choose_line
from overclocked.detectors import Session

# ── colour palette (dark-mode safe) ───────────────────────────────────────────

_ACTIVE = "#E8730A"  # brighter amber — clearer project rows on light translucent menus
_HEADER = "#6F5543"  # warm ink — stronger contrast than white on light menus
_DIM = "#978274"  # muted taupe — still subdued, but not washed out
_WITTY = "#B59F90"  # warm parchment tint — visible without shouting
_STATS = "#9D887A"  # medium warm grey — more readable in the stats block

# ── SF Symbol per tool ────────────────────────────────────────────────────────

_TOOL_SYMBOLS: dict[str, str] = {
    "claude": "terminal",
    "cursor": "cursorarrow.rays",
    "codex": "cube",
}

_TOOL_LABELS: dict[str, str] = {
    "claude": "Claude Code",
    "cursor": "Cursor",
    "codex": "Codex",
}

_TOOL_ORDER = ["claude", "cursor", "codex"]

_TOOL_ALIASES: dict[str, str] = {
    "cursor_editor": "cursor",
    "cursor_agent": "cursor",
}


# ── SwiftBar param helper ─────────────────────────────────────────────────────


def _p(**kwargs: str | int) -> str:
    """Return a SwiftBar param string: ' | key=value key=value ...'"""
    parts = " ".join(f"{k.replace('_', '')}={v}" for k, v in kwargs.items())
    return f" | {parts}"


def _swiftbar_safe(s: str) -> str:
    """Sanitise a string for use in a SwiftBar menu line.

    Replaces | with ¦ and strips newlines and other C0 control characters.
    """
    s = s.replace("|", "¦")
    return "".join(ch for ch in s if ch == "\t" or ord(ch) >= 32)


# ── public helpers ────────────────────────────────────────────────────────────


def humanise_delta(seconds: float) -> str:
    """Return a humanised time delta string."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def menu_bar_line(active: int) -> str:
    """Return the compact menu bar string."""
    return f"🧠  {active}"


def _sparkline_str(values: list[int]) -> str:
    """Render a list of ints as a Unicode block sparkline."""
    blocks = " ▁▂▃▄▅▆▇█"
    max_val = max(values) or 1
    return "".join(blocks[min(8, round(v / max_val * 8))] for v in values)


@dataclass
class RenderState:
    sessions: list[Session]
    conn: sqlite3.Connection | None = None


def _group_sessions_by_project(sessions: list[Session]) -> dict[str, list[tuple[str, int]]]:
    """Return grouped project counts for each tool, sorted by count desc then name."""
    grouped: dict[str, dict[str, int]] = {tool: {} for tool in _TOOL_ORDER}
    for session in sessions:
        project = session.project or "—"
        tool_projects = grouped.setdefault(session.tool, {})
        tool_projects[project] = tool_projects.get(project, 0) + 1

    ordered: dict[str, list[tuple[str, int]]] = {}
    for tool, tool_projects in grouped.items():
        ordered[tool] = sorted(tool_projects.items(), key=lambda item: (-item[1], item[0]))
    return ordered


def _project_status_suffix(sessions: list[Session], tool: str, project_name: str) -> str:
    """Compact status tag when all rows share one abtop-style status, else ellipsis."""
    statuses: list[str] = []
    for s in sessions:
        if _TOOL_ALIASES.get(s.tool, s.tool) != tool:
            continue
        if (s.project or "—") != project_name:
            continue
        if s.status:
            statuses.append(s.status)
    if not statuses:
        return ""
    uniq = sorted(set(statuses))
    if len(uniq) == 1:
        return f" · {_swiftbar_safe(uniq[0])}"
    return " · …"


def dropdown(state: RenderState) -> str:
    """Return the full SwiftBar dropdown text (menu bar line + dropdown body)."""
    sessions = state.sessions
    conn = state.conn

    active = len(sessions)

    by_tool: dict[str, list[Session]] = defaultdict(list)
    for s in sessions:
        by_tool[_TOOL_ALIASES.get(s.tool, s.tool)].append(s)

    aliased = [
        Session(
            tool=_TOOL_ALIASES.get(s.tool, s.tool),
            pid=s.pid,
            cwd=s.cwd,
            project=s.project,
            status=s.status,
        )
        for s in sessions
    ]
    grouped_projects = _group_sessions_by_project(aliased)

    lines: list[str] = []

    # ── menu bar line ──────────────────────────────────────────────────────────
    lines.append(menu_bar_line(active))
    lines.append("---")

    # ── per-tool groups ────────────────────────────────────────────────────────
    for tool in _TOOL_ORDER:
        tool_sessions = by_tool.get(tool, [])
        count = len(tool_sessions)
        label = _TOOL_LABELS.get(tool, tool)
        symbol = _TOOL_SYMBOLS.get(tool, "")

        if count > 0:
            params = _p(color=_HEADER, size=13, sfimage=symbol)
            lines.append(f"{label}  {count}{params}")

            for project_name, project_count in grouped_projects.get(tool, []):
                project = _swiftbar_safe(project_name)
                st = _project_status_suffix(sessions, tool, project_name)
                params = _p(color=_ACTIVE, size=12, trim="false")
                lines.append(f"  {project}  {project_count}{st}{params}")
        else:
            params = _p(color=_DIM, size=11)
            lines.append(f"{label}  0{params}")

    lines.append("---")

    # ── today's stats + witty (single snapshot query) ────────────────────────
    hist: TodayHistoryContext | None = None
    if conn is not None:
        hist = TodayHistoryContext.load(conn)

    # ── witty line ────────────────────────────────────────────────────────────
    witty = _swiftbar_safe(choose_line(active, conn=conn, ctx=hist))
    params = _p(font="Georgia-Italic", color=_WITTY, size=12)
    lines.append(f"{witty}{params}")
    lines.append("---")

    # ── today's stats ─────────────────────────────────────────────────────────
    if conn is not None and hist is not None:
        peak_count, peak_ts = hist.today_peak()
        avg = hist.today_average()
        spark = hist.today_sparkline()
        spark_str = _sparkline_str(spark)

        stats_params = _p(color=_STATS)
        spark_params = _p(font="Menlo", size=14, color=_STATS, trim="false")

        if peak_ts:
            peak_time = _swiftbar_safe(datetime.fromtimestamp(peak_ts).strftime("%H:%M"))
            lines.append(f"📈 Today: peak {peak_count} @ {peak_time}  ·  avg {avg}{stats_params}")
        else:
            lines.append(f"📈 Today: avg {avg}{stats_params}")
        lines.append(f"  {spark_str}{spark_params}")
    else:
        lines.append(f"📈 No history yet{_p(color=_STATS)}")

    return "\n".join(lines)
