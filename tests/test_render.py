"""Tests for the rendering layer."""

from __future__ import annotations

from overclocked.config import Config
from overclocked.detectors import Session
from overclocked.identity import project_label
from overclocked.render import RenderState, _swiftbar_safe, dropdown, humanise_delta, menu_bar_line

# ── humanise_delta ────────────────────────────────────────────────────────────


def test_humanise_seconds():
    assert humanise_delta(8) == "8s ago"


def test_humanise_minutes():
    assert humanise_delta(240) == "4m"


def test_humanise_hours():
    assert humanise_delta(7200) == "2h"


def test_humanise_hours_non_exact_multiple():
    """7290 s is 2 h 1.5 min, should round down to 2h."""
    assert humanise_delta(7290) == "2h"


def test_humanise_zero():
    assert humanise_delta(0) == "0s ago"


# ── menu_bar_line ─────────────────────────────────────────────────────────────


def test_menu_bar_zero():
    assert menu_bar_line(0) == "🧠  0"


def test_menu_bar_nonzero():
    assert menu_bar_line(3) == "🧠  3"


# ── _swiftbar_safe ────────────────────────────────────────────────────────────


def test_swiftbar_safe_replaces_pipe():
    assert "|" not in _swiftbar_safe("foo|bar")
    assert "¦" in _swiftbar_safe("foo|bar")


def test_swiftbar_safe_strips_newline():
    result = _swiftbar_safe("foo\nbar")
    assert "\n" not in result


def test_swiftbar_safe_strips_carriage_return():
    result = _swiftbar_safe("foo\rbar")
    assert "\r" not in result


def test_swiftbar_safe_passthrough():
    assert _swiftbar_safe("normal project name") == "normal project name"


# ── dropdown ─────────────────────────────────────────────────────────────────


def test_dropdown_zero_sessions():
    state = RenderState(sessions=[])
    output = dropdown(state)
    assert "🧠  0" in output
    assert "Claude Code" in output
    assert "Cursor" in output
    assert "Codex" in output


def test_dropdown_with_sessions():
    sessions = [
        Session(tool="claude", pid=1, cwd="/dev/overclocked", project="overclocked"),
        Session(tool="cursor_editor", pid=2, cwd=None),
    ]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "🧠  2" in output
    assert "🔥" not in output
    assert "Claude Code" in output
    assert "Cursor" in output
    assert "overclocked  1" in output
    assert "—  1" in output


def test_dropdown_redacted_project():
    """Session with project='redacted' shows 'redacted', not the raw cwd."""
    cfg = Config(redact_paths=["~/clients/"])
    import os

    cwd = os.path.join(os.path.expanduser("~"), "clients", "acme")
    project = project_label(cwd, cfg)
    assert project == "redacted"

    sessions = [Session(tool="codex", pid=3, cwd=cwd, project=project)]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "redacted" in output
    assert "acme" not in output


def test_dropdown_no_cwd_shows_dash():
    """Session with no project shows —."""
    sessions = [Session(tool="codex", pid=4, cwd=None, project=None)]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "—  1" in output


def test_dropdown_no_history_line():
    state = RenderState(sessions=[])
    output = dropdown(state)
    assert "No history yet" in output


def test_active_session_row_has_orange_colour():
    sessions = [Session(tool="claude", pid=1, cwd="/dev/proj", project="proj")]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "#E8730A" in output


def test_session_row_has_no_hot_suffix():
    sessions = [Session(tool="codex", pid=2, cwd="/dev/proj", project="proj")]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "· hot" not in output


def test_dropdown_groups_same_project_into_single_summary_row():
    sessions = [
        Session(tool="claude", pid=1, cwd="/dev/almanac", project="almanac"),
        Session(tool="claude", pid=2, cwd="/dev/almanac", project="almanac"),
        Session(tool="claude", pid=3, cwd="/dev/almanac", project="almanac"),
    ]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "Claude Code  3" in output
    assert output.count("almanac  3") == 1
    assert "almanac  1" not in output


def test_dropdown_groups_sorted_by_count_then_name():
    sessions = [
        Session(tool="codex", pid=1, cwd="/dev/beta", project="beta"),
        Session(tool="codex", pid=2, cwd="/dev/beta", project="beta"),
        Session(tool="codex", pid=3, cwd="/dev/alpha", project="alpha"),
        Session(tool="codex", pid=4, cwd="/dev/gamma", project="gamma"),
        Session(tool="codex", pid=5, cwd="/dev/gamma", project="gamma"),
    ]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    lines = output.splitlines()
    project_lines = [line for line in lines if line.startswith("  ")]
    codex_lines = [
        line
        for line in project_lines
        if any(name in line for name in ("alpha", "beta", "gamma"))
    ]
    assert codex_lines[:3] == [
        "  beta  2 | color=#E8730A size=12 trim=false",
        "  gamma  2 | color=#E8730A size=12 trim=false",
        "  alpha  1 | color=#E8730A size=12 trim=false",
    ]


def test_dropdown_groups_none_projects_under_single_dash_row():
    sessions = [
        Session(tool="cursor_agent", pid=1, cwd=None, project=None),
        Session(tool="cursor_agent", pid=2, cwd=None, project=None),
    ]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "—  2" in output
    assert output.count("—  2") == 1


def test_witty_line_no_quotes_and_italic():
    state = RenderState(sessions=[])
    output = dropdown(state)
    assert '"nothing running' not in output
    assert "Georgia-Italic" in output


def test_sparkline_uses_menlo(tmp_path):
    import time

    from overclocked.storage import connect

    conn = connect(tmp_path / "test.db")
    ts = int(time.time()) - 100
    conn.execute(
        "INSERT INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ts, 3, "{}"),
    )
    conn.commit()
    state = RenderState(sessions=[], conn=conn)
    output = dropdown(state)
    assert "Menlo" in output


def test_dropdown_pipe_in_project_name():
    """Project name with | must not break SwiftBar rows."""
    sessions = [Session(tool="claude", pid=1, cwd="/dev/foo|bar", project="foo|bar")]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    # The | should have been replaced; no raw | in session rows
    lines = output.splitlines()
    session_lines = [ln for ln in lines if "foo" in ln]
    assert all("|" not in ln.split("|")[0] for ln in session_lines)


def test_dropdown_newline_in_project_name():
    """Newline in project name must be stripped."""
    sessions = [Session(tool="claude", pid=1, cwd=None, project="foo\nbar")]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "\n\n" not in output  # no extra blank line from newline in project


def test_dropdown_pipe_in_witty_line(monkeypatch):
    """Witty line with | must be sanitised."""
    monkeypatch.setattr(
        "overclocked.render.choose_line",
        lambda count, conn=None, ctx=None: "foo|bar",
    )
    state = RenderState(sessions=[])
    output = dropdown(state)
    lines = [ln for ln in output.splitlines() if "foo" in ln]
    assert lines
    # The rendered witty line should use ¦ not |
    assert "foo¦bar" in output
