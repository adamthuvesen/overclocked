"""Tests for the rendering layer."""

from __future__ import annotations

from overclocked.config import Config
from overclocked.detectors import Session
from overclocked.identity import project_label
from overclocked.render import RenderState, _swiftbar_safe, dropdown, menu_bar_line

# ── menu_bar_line ─────────────────────────────────────────────────────────────


def test_menu_bar_zero():
    assert menu_bar_line(0) == "👾  0"


def test_menu_bar_nonzero():
    assert menu_bar_line(3) == "👾  3"


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


def test_dropdown_zero_sessions_hides_tool_rows():
    state = RenderState(sessions=[])
    output = dropdown(state)
    assert "👾  0" in output
    assert "Claude Code" not in output
    assert "Cursor" not in output
    assert "Codex" not in output


def test_dropdown_with_sessions():
    sessions = [
        Session(tool="claude", pid=1, cwd="/dev/overclocked", project="overclocked"),
        Session(tool="cursor_editor", pid=2, cwd=None),
    ]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "👾  2" in output
    assert "🔥" not in output
    assert "Claude Code" in output
    assert "Cursor" in output
    assert "  overclocked" in output
    assert "  —" in output
    assert "overclocked  1" not in output
    assert "—  1" not in output


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
    assert "  —" in output
    assert "—  1" not in output


def test_dropdown_no_db_shows_no_history_line_without_chart_emoji():
    state = RenderState(sessions=[])
    output = dropdown(state)
    assert "📈" not in output
    assert "No history yet" in output
    assert "nothing running" in output


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


def test_dropdown_shows_status_suffix():
    sessions = [
        Session(
            tool="claude",
            pid=1,
            cwd="/dev/overclocked",
            project="overclocked",
            status="working",
        ),
    ]
    output = dropdown(RenderState(sessions=sessions, config=Config(session_status=True)))
    assert "· working" in output


def test_dropdown_default_hides_status_suffix():
    sessions = [
        Session(tool="claude", pid=1, cwd="/dev/x", project="x", status="working"),
    ]
    output = dropdown(RenderState(sessions=sessions))
    assert "· working" not in output


def test_dropdown_mixed_status_shows_separate_rows():
    sessions = [
        Session(tool="claude", pid=1, cwd="/dev/x", project="x", status="working"),
        Session(tool="claude", pid=2, cwd="/dev/x", project="x", status="waiting"),
    ]
    output = dropdown(RenderState(sessions=sessions, config=Config(session_status=True)))
    assert "· working" in output
    assert "· waiting" in output
    assert "· …" not in output


def test_dropdown_shows_metrics_suffix():
    sessions = [
        Session(
            tool="claude",
            pid=1,
            cwd="/dev/x",
            project="x",
            model="claude-sonnet-4-20250514",
            input_tokens=1500,
            output_tokens=200,
        ),
    ]
    output = dropdown(RenderState(sessions=sessions, config=Config(session_metrics=True)))
    assert "1.5k" in output  # input_tokens=1500; output_tokens excluded from context total
    assert "claude-sonnet-4-20250514" in output or "claude-sonnet-4-202505" in output


def test_dropdown_default_hides_metrics_suffix():
    sessions = [
        Session(
            tool="claude",
            pid=1,
            cwd="/dev/x",
            project="x",
            model="unique-model-xyz-991",
            input_tokens=99999,
            output_tokens=1,
        ),
    ]
    output = dropdown(RenderState(sessions=sessions))
    assert "unique-model-xyz" not in output
    assert "100k" not in output


def test_dropdown_session_metrics_false_hides_suffix():
    sessions = [
        Session(
            tool="claude",
            pid=1,
            cwd="/dev/x",
            project="x",
            model="unique-model-xyz-991",
            input_tokens=99999,
            output_tokens=1,
        ),
    ]
    output = dropdown(RenderState(sessions=sessions, config=Config(session_metrics=False)))
    assert "unique-model-xyz" not in output
    assert "100k" not in output


def test_dropdown_status_and_metrics_compose_in_order():
    sessions = [
        Session(
            tool="claude",
            pid=1,
            cwd="/dev/x",
            project="x",
            status="waiting",
            model="gpt-5",
            input_tokens=37000,
        ),
    ]
    output = dropdown(
        RenderState(
            sessions=sessions,
            config=Config(session_status=True, session_metrics=True),
        ),
    )
    assert "x · waiting · gpt-5 · 37k" in output


def test_dropdown_cursor_rows_ignore_metric_fields():
    sessions = [
        Session(
            tool="cursor_editor",
            pid=1,
            cwd="/dev/w",
            project="w",
            model="should-not-appear",
            input_tokens=50000,
        ),
    ]
    output = dropdown(RenderState(sessions=sessions, config=Config(session_metrics=True)))
    assert "should-not-appear" not in output
    assert "50k" not in output and "50000" not in output


def test_dropdown_mixed_models_one_row_each_in_metrics():
    sessions = [
        Session(
            tool="codex",
            pid=1,
            cwd="/dev/x",
            project="x",
            model="gpt-a",
            input_tokens=100,
            output_tokens=0,
        ),
        Session(
            tool="codex",
            pid=2,
            cwd="/dev/x",
            project="x",
            model="gpt-b",
            input_tokens=200,
            output_tokens=0,
        ),
    ]
    output = dropdown(RenderState(sessions=sessions, config=Config(session_metrics=True)))
    assert "gpt-a" in output
    assert "gpt-b" in output
    assert "· …" not in output


def test_dropdown_same_project_stacked_as_one_row_per_session():
    sessions = [
        Session(tool="claude", pid=1, cwd="/dev/almanac", project="almanac"),
        Session(tool="claude", pid=2, cwd="/dev/almanac", project="almanac"),
        Session(tool="claude", pid=3, cwd="/dev/almanac", project="almanac"),
    ]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert "Claude Code" in output
    assert "Claude Code  3" not in output
    almanac_rows = [ln for ln in output.splitlines() if "  almanac" in ln]
    assert len(almanac_rows) == 3


def test_dropdown_session_rows_sorted_by_project_then_pid():
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
        line for line in project_lines if any(name in line for name in ("alpha", "beta", "gamma"))
    ]
    assert codex_lines == [
        "  alpha | color=#E8730A size=12 trim=false",
        "  beta | color=#E8730A size=12 trim=false",
        "  beta | color=#E8730A size=12 trim=false",
        "  gamma | color=#E8730A size=12 trim=false",
        "  gamma | color=#E8730A size=12 trim=false",
    ]


def test_dropdown_groups_none_projects_as_one_row_per_session():
    sessions = [
        Session(tool="cursor_agent", pid=1, cwd=None, project=None),
        Session(tool="cursor_agent", pid=2, cwd=None, project=None),
    ]
    state = RenderState(sessions=sessions)
    output = dropdown(state)
    assert output.count("  — |") == 2
    assert "—  2" not in output


def test_witty_line_no_quotes_and_italic():
    state = RenderState(sessions=[])
    output = dropdown(state)
    assert '"nothing running' not in output
    assert "Georgia-Italic" in output


def test_dropdown_sparkline_uses_menlo_no_chart_emoji(tmp_path):
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
    assert "📈" not in output
    assert "Today:" in output
    assert "Menlo" in output
    assert "Georgia-Italic" in output


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
