"""SwiftBar-formatted rendering of session state."""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from overclocked.aggregates import TodayHistoryContext
from overclocked.config import Config
from overclocked.copy import choose_line
from overclocked.detectors import Session

# ── colour palette (dark-mode safe) ───────────────────────────────────────────

_ACTIVE = "#E8730A"  # brighter amber — clearer project rows on light translucent menus
_HEADER = "#6F5543"  # warm ink — stronger contrast than white on light menus
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


def _session_token_total(s: Session) -> int:
    # Context-window usage: input side only (matches what tools show as "37k context").
    # output_tokens are generated tokens, not in-context tokens.
    return (s.input_tokens or 0) + (s.cache_read or 0) + (s.cache_create or 0)


def _truncate_model_name(model: str, max_len: int = 24) -> str:
    m = _swiftbar_safe(model)
    if len(m) <= max_len:
        return m
    return m[: max_len - 1] + "…"


def _format_token_total(n: int) -> str:
    if n >= 1_000_000:
        v = n / 1_000_000
        s = f"{v:.1f}".rstrip("0").rstrip(".")
        return f"{s}M"
    if n >= 1000:
        v = n / 1000
        if v >= 100:
            return f"{round(v)}k"
        s = f"{v:.1f}".rstrip("0").rstrip(".")
        return f"{s}k"
    return str(int(n))


def _session_status_suffix(s: Session) -> str:
    if not s.status:
        return ""
    return f" · {_swiftbar_safe(s.status)}"


def _session_metrics_suffix(s: Session, *, session_metrics: bool) -> str:
    """Model + token hint for one session (Cursor rows omit metrics)."""
    if not session_metrics:
        return ""
    if s.tool in ("cursor_editor", "cursor_agent"):
        return ""
    parts: list[str] = []
    if s.model:
        parts.append(_truncate_model_name(s.model))
    tot = _session_token_total(s)
    if tot > 0:
        parts.append(_format_token_total(tot))
    if not parts:
        return ""
    return " · " + " · ".join(parts)


def _sessions_for_tool_ordered(sessions: list[Session], tool: str) -> list[Session]:
    """Sessions for a display tool bucket, sorted by project then PID."""
    rows = [s for s in sessions if _TOOL_ALIASES.get(s.tool, s.tool) == tool]
    rows.sort(key=lambda s: (s.project or "—", s.pid))
    return rows


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
    config: Config = field(default_factory=Config)


def dropdown(state: RenderState) -> str:
    """Return the full SwiftBar dropdown text (menu bar line + dropdown body)."""
    sessions = state.sessions
    conn = state.conn

    active = len(sessions)

    by_tool: dict[str, list[Session]] = defaultdict(list)
    for s in sessions:
        by_tool[_TOOL_ALIASES.get(s.tool, s.tool)].append(s)

    lines: list[str] = []

    # ── menu bar line ──────────────────────────────────────────────────────────
    lines.append(menu_bar_line(active))

    # ── per-tool groups (omit tools with zero active sessions) ─────────────────
    any_tool = False
    for tool in _TOOL_ORDER:
        tool_sessions = by_tool.get(tool, [])
        if not tool_sessions:
            continue
        if not any_tool:
            lines.append("---")
            any_tool = True
        label = _TOOL_LABELS.get(tool, tool)
        symbol = _TOOL_SYMBOLS.get(tool, "")
        params = _p(color=_HEADER, size=13, sfimage=symbol)
        lines.append(f"{label}{params}")

        for s in _sessions_for_tool_ordered(sessions, tool):
            project = _swiftbar_safe(s.project or "—")
            st = _session_status_suffix(s)
            mx = _session_metrics_suffix(s, session_metrics=state.config.session_metrics)
            row_params = _p(color=_ACTIVE, size=12, trim="false")
            lines.append(f"  {project}{st}{mx}{row_params}")

    lines.append("---")

    # ── witty line (uses history when conn is set) ────────────────────────────
    hist: TodayHistoryContext | None = None
    if conn is not None:
        hist = TodayHistoryContext.load(conn)
    witty = _swiftbar_safe(choose_line(active, conn=conn, ctx=hist))
    params = _p(font="Georgia-Italic", color=_WITTY, size=12)
    lines.append(f"{witty}{params}")
    lines.append("---")

    # ── today's stats + sparkline (no leading chart emoji — keeps the row calm)
    if conn is not None and hist is not None:
        peak_count, peak_ts = hist.today_peak()
        avg = hist.today_average()
        spark = hist.today_sparkline()
        spark_str = _sparkline_str(spark)

        stats_params = _p(color=_STATS)
        spark_params = _p(font="Menlo", size=14, color=_STATS, trim="false")

        if peak_ts:
            peak_time = _swiftbar_safe(datetime.fromtimestamp(peak_ts).strftime("%H:%M"))
            lines.append(f"Today: peak {peak_count} @ {peak_time}  ·  avg {avg}{stats_params}")
        else:
            lines.append(f"Today: avg {avg}{stats_params}")
        lines.append(f"  {spark_str}{spark_params}")
    else:
        lines.append(f"No history yet{_p(color=_STATS)}")

    return "\n".join(lines)
