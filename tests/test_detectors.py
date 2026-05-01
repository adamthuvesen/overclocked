"""Tests for session detectors."""

from __future__ import annotations

import json
import subprocess
import time
from datetime import UTC
from pathlib import Path

from overclocked.config import Config
from overclocked.detectors import (
    CodexTickData,
    PsRow,
    Session,
    _claude_pgrep_all,
    _cursor_project_workspace_cwd,
    _merge_cursor_editor_and_agent,
    claude_cli_session_is_active,
    codex_app_session_is_active,
    cpu_is_active,
    cursor_agent_session_is_active,
    has_active_descendant,
    is_descendant_of,
    list_claude_app_sessions,
    list_claude_sessions,
    list_codex_app_sessions,
    list_codex_sessions,
    list_cursor_agent_sessions,
    list_cursor_editor_windows,
    raw_session_keys,
    stable_sessions_from_keys,
    tick,
)

# ── is_descendant_of ──────────────────────────────────────────────────────────


def test_descendant_not_ralph(monkeypatch):
    """Normal process with no ralph ancestor returns False."""
    import overclocked.detectors as d

    monkeypatch.setattr(
        d, "_ps_table", {99999: d.PsRow(ppid=1, tty="??", pcpu=0.0, command="python3 script.py")}
    )
    assert not is_descendant_of(99999, ["ralph"])


def test_descendant_is_ralph(monkeypatch):
    """Process with ralph in ancestor chain returns True."""
    import overclocked.detectors as d

    table = {
        100: d.PsRow(ppid=1, tty="??", pcpu=0.0, command="/usr/local/bin/ralph run"),
        200: d.PsRow(ppid=100, tty="??", pcpu=0.0, command="/usr/bin/something 200"),
    }
    monkeypatch.setattr(d, "_ps_table", table)
    assert is_descendant_of(200, ["ralph"])


# ── claude_cli_session_is_active ──────────────────────────────────────────────


def test_claude_active_recent_file(tmp_path, monkeypatch):
    projects = tmp_path / ".claude" / "projects" / "myproject"
    projects.mkdir(parents=True)
    (projects / "conversation.jsonl").write_text("data")
    import overclocked.detectors as d

    monkeypatch.setattr(d, "_CLAUDE_PROJECTS_DIR", projects.parent)
    monkeypatch.setattr(d, "_resolve_cwd_cached", lambda pid: "/myproject")
    assert claude_cli_session_is_active(12345)


def test_claude_inactive_old_file(tmp_path, monkeypatch):
    projects = tmp_path / ".claude" / "projects" / "myproject"
    projects.mkdir(parents=True)
    conv = projects / "conversation.jsonl"
    conv.write_text("old")
    import os

    old_time = time.time() - 2000
    os.utime(conv, (old_time, old_time))
    import overclocked.detectors as d

    monkeypatch.setattr(d, "_CLAUDE_PROJECTS_DIR", projects.parent)
    monkeypatch.setattr(d, "_resolve_cwd_cached", lambda pid: "/myproject")
    assert not claude_cli_session_is_active(12345)


def test_claude_inactive_empty_project_dir(tmp_path, monkeypatch):
    projects = tmp_path / ".claude" / "projects" / "myproject"
    projects.mkdir(parents=True)
    import overclocked.detectors as d

    monkeypatch.setattr(d, "_CLAUDE_PROJECTS_DIR", projects.parent)
    monkeypatch.setattr(d, "_resolve_cwd_cached", lambda pid: "/myproject")
    monkeypatch.setattr(d, "_cpu_percent", lambda pid: 0.0)
    assert not d.claude_cli_session_is_active(1)


def test_claude_active_cpu_despite_stale_files(tmp_path, monkeypatch):
    projects = tmp_path / ".claude" / "projects" / "myproject"
    projects.mkdir(parents=True)
    conv = projects / "conversation.jsonl"
    conv.write_text("old")
    import os

    import overclocked.detectors as d

    os.utime(conv, (time.time() - 2000, time.time() - 2000))
    monkeypatch.setattr(d, "_CLAUDE_PROJECTS_DIR", projects.parent)
    monkeypatch.setattr(d, "_resolve_cwd_cached", lambda pid: "/myproject")
    monkeypatch.setattr(d, "_cpu_percent", lambda pid: 50.0)
    assert d.claude_cli_session_is_active(1)


def test_claude_inactive_without_cwd(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._resolve_cwd_cached", lambda pid: None)
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 0.0)
    assert not claude_cli_session_is_active(1)


# ── cpu_is_active ─────────────────────────────────────────────────────────────


def test_cpu_is_active_above_threshold(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 50.0)
    assert cpu_is_active(12345)


def test_cpu_not_active(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 1.0)
    assert not cpu_is_active(12345)


# ── subprocess robustness ─────────────────────────────────────────────────────


def test_safe_check_output_file_not_found(monkeypatch):
    """_safe_check_output returns None when binary is missing."""
    from overclocked._subprocess import _safe_check_output

    monkeypatch.setattr(
        "subprocess.check_output",
        lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError("not found")),
    )
    assert _safe_check_output(["nosuchbin"]) is None


def test_safe_check_output_timeout(monkeypatch):
    """_safe_check_output returns None on TimeoutExpired."""
    from overclocked._subprocess import _safe_check_output

    def raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd=["nosuchbin"], timeout=2)

    monkeypatch.setattr("subprocess.check_output", raise_timeout)
    assert _safe_check_output(["nosuchbin"]) is None


def test_tick_survives_pgrep_file_not_found(tmp_path, monkeypatch):
    """tick() returns [] rather than crashing when pgrep is missing."""
    monkeypatch.setattr(
        "overclocked.detectors._safe_check_output",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path / "claude")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path / "cursor")
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path / "codex")
    first = tick(Config())
    k1 = raw_session_keys(first)
    second = tick(Config())
    assert stable_sessions_from_keys(second, k1) == []


# ── cursor_agent_session_is_active ────────────────────────────────────────────


def test_cursor_agent_active_recent_transcript(tmp_path):
    transcripts = tmp_path / "agent-transcripts" / "abc"
    transcripts.mkdir(parents=True)
    (transcripts / "abc.jsonl").write_text("{}")
    assert cursor_agent_session_is_active(tmp_path)


def test_cursor_agent_inactive_stale_transcript(tmp_path):
    import os

    transcripts = tmp_path / "agent-transcripts" / "abc"
    transcripts.mkdir(parents=True)
    f = transcripts / "abc.jsonl"
    f.write_text("{}")
    old_time = time.time() - 2000
    os.utime(f, (old_time, old_time))
    assert not cursor_agent_session_is_active(tmp_path)


def test_cursor_agent_active_when_parent_dir_stale_but_jsonl_fresh(tmp_path):
    """Appending to jsonl does not refresh agent-transcripts/ mtime on many filesystems."""
    import os

    tx_root = tmp_path / "agent-transcripts"
    nested = tx_root / "uuid"
    nested.mkdir(parents=True)
    f = nested / "t.jsonl"
    f.write_text("{}")
    old = time.time() - 2000
    os.utime(tx_root, (old, old))
    assert cursor_agent_session_is_active(tmp_path)


def test_list_cursor_agent_sessions_detects_recent_transcript(tmp_path, monkeypatch):
    proj = tmp_path / "Users-me-dev-proj"
    transcripts = proj / "agent-transcripts" / "uuid"
    transcripts.mkdir(parents=True)
    (transcripts / "uuid.jsonl").write_text("{}")
    terminals = proj / "terminals"
    terminals.mkdir()
    (terminals / "1.txt").write_text("---\npid: 99\ncwd: /Users/me/dev/proj\n")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    sessions = list_cursor_agent_sessions()
    assert len(sessions) == 1
    assert sessions[0].tool == "cursor_agent"
    assert sessions[0].cwd == "/Users/me/dev/proj"


def test_list_cursor_agent_sessions_ignores_old_transcripts(tmp_path, monkeypatch):
    import os

    proj = tmp_path / "Users-me-dev-old"
    transcripts = proj / "agent-transcripts" / "uuid"
    transcripts.mkdir(parents=True)
    f = transcripts / "uuid.jsonl"
    f.write_text("{}")
    old_time = time.time() - 2000
    os.utime(f, (old_time, old_time))
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    sessions = list_cursor_agent_sessions()
    assert sessions == []


def test_list_cursor_agent_sessions_skips_projects_without_cwd(tmp_path, monkeypatch):
    proj = tmp_path / "Users-me"
    transcripts = proj / "agent-transcripts" / "uuid"
    transcripts.mkdir(parents=True)
    (transcripts / "uuid.jsonl").write_text("{}")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    sessions = list_cursor_agent_sessions()
    assert sessions == []


def _make_cursor_workspace(
    tmp_path: Path,
    *,
    project_slug: str,
    cwd: str,
    session_id: str,
) -> Path:
    """Build a minimal Cursor workspace dir with a fresh parent transcript."""
    proj = tmp_path / project_slug
    transcripts = proj / "agent-transcripts" / session_id
    transcripts.mkdir(parents=True)
    (transcripts / f"{session_id}.jsonl").write_text("{}")
    terminals = proj / "terminals"
    terminals.mkdir()
    (terminals / "1.txt").write_text(f"---\npid: 99\ncwd: {cwd}\n")
    return proj


def test_list_cursor_agent_sessions_populates_session_id(tmp_path, monkeypatch):
    """Parent cursor_agent rows carry session_id derived from the transcript dir name."""
    sid = "11111111-2222-3333-4444-555555555555"
    _make_cursor_workspace(
        tmp_path, project_slug="Users-me-dev-proj", cwd="/Users/me/dev/proj", session_id=sid
    )
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    sessions = list_cursor_agent_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == sid


def test_list_cursor_agent_sessions_attaches_live_subagents(tmp_path, monkeypatch):
    """A cursor_agent parent with two recent subagent jsonls emits two subagent rows."""
    sid = "11111111-2222-3333-4444-555555555555"
    proj = _make_cursor_workspace(
        tmp_path, project_slug="Users-me-dev-proj", cwd="/Users/me/dev/proj", session_id=sid
    )
    sub_dir = proj / "agent-transcripts" / sid / "subagents"
    sub_dir.mkdir()
    (sub_dir / "aaa1.jsonl").write_text("{}")
    (sub_dir / "bbb2.jsonl").write_text("{}")

    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    sessions = list_cursor_agent_sessions(Config())

    parents = [s for s in sessions if not s.is_subagent]
    children = [s for s in sessions if s.is_subagent]
    assert len(parents) == 1
    assert parents[0].session_id == sid
    assert len(children) == 2
    assert {c.agent_id for c in children} == {"aaa1", "bbb2"}
    assert all(c.parent_session_id == sid for c in children)
    assert all(c.tool == "cursor_agent" for c in children)
    assert all(c.cwd == "/Users/me/dev/proj" for c in children)
    assert all(c.synthetic for c in children)


def test_list_cursor_agent_sessions_ignores_stale_subagents(tmp_path, monkeypatch):
    """Subagent jsonls older than the liveness window are not emitted."""
    import os

    sid = "11111111-2222-3333-4444-555555555555"
    proj = _make_cursor_workspace(
        tmp_path, project_slug="Users-me-dev-proj", cwd="/Users/me/dev/proj", session_id=sid
    )
    sub_dir = proj / "agent-transcripts" / sid / "subagents"
    sub_dir.mkdir()
    fresh = sub_dir / "fresh.jsonl"
    stale = sub_dir / "stale.jsonl"
    fresh.write_text("{}")
    stale.write_text("{}")
    old = time.time() - 2000
    os.utime(stale, (old, old))

    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    sessions = list_cursor_agent_sessions(Config())
    children = [s for s in sessions if s.is_subagent]
    assert [c.agent_id for c in children] == ["fresh"]


def test_list_cursor_agent_sessions_show_subagents_false_omits_children(tmp_path, monkeypatch):
    """show_subagents=False suppresses Cursor subagent rows."""
    sid = "deadbeef-0000-0000-0000-000000000000"
    proj = _make_cursor_workspace(
        tmp_path, project_slug="Users-me-dev-proj", cwd="/Users/me/dev/proj", session_id=sid
    )
    sub_dir = proj / "agent-transcripts" / sid / "subagents"
    sub_dir.mkdir()
    (sub_dir / "x.jsonl").write_text("{}")

    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    sessions = list_cursor_agent_sessions(Config(show_subagents=False))
    assert all(not s.is_subagent for s in sessions)


# ── list_cursor_editor_windows ────────────────────────────────────────────────


def _seed_cursor_project(tmp_path, name: str, cwd: str, *, fresh: bool = True) -> None:
    """Create a fake Cursor project dir with a terminals/<n>.txt that exposes cwd.

    Mirrors the structure list_cursor_editor_windows reads from in production.
    """
    import os

    proj = tmp_path / name
    (proj / "terminals").mkdir(parents=True)
    term = proj / "terminals" / "1.txt"
    term.write_text(f"---\npid: 1\ncwd: {cwd}\n---\n")
    if not fresh:
        old = time.time() - 2000
        os.utime(term, (old, old))
        os.utime(proj / "terminals", (old, old))
        os.utime(proj, (old, old))


def test_list_cursor_editor_skips_when_cursor_not_running(tmp_path, monkeypatch):
    _seed_cursor_project(tmp_path, "Users-me-dev-proj", "/Users/me/dev/proj")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [])
    assert list_cursor_editor_windows() == []


def test_list_cursor_editor_skips_without_project_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [7001])
    assert list_cursor_editor_windows() == []


def test_list_cursor_editor_skips_stale_project(tmp_path, monkeypatch):
    _seed_cursor_project(tmp_path, "Users-me-dev-proj", "/Users/me/dev/proj", fresh=False)
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [7001, 7002])
    assert list_cursor_editor_windows() == []


def test_list_cursor_editor_skips_when_only_mcps_fresh(tmp_path, monkeypatch):
    """Opening Cursor can refresh MCP descriptors without local workspace activity."""
    _seed_cursor_project(tmp_path, "Users-me-dev-proj", "/Users/me/dev/proj", fresh=False)
    mcps = tmp_path / "Users-me-dev-proj" / "mcps"
    mcps.mkdir(parents=True)
    (mcps / "server.json").write_text("{}")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [7001])
    assert list_cursor_editor_windows() == []


def test_list_cursor_editor_keeps_fresh(tmp_path, monkeypatch):
    _seed_cursor_project(tmp_path, "Users-me-dev-proj", "/Users/me/dev/proj")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [7001, 7002])
    sessions = list_cursor_editor_windows()
    assert len(sessions) == 1
    assert sessions[0].tool == "cursor_editor"
    assert sessions[0].cwd == "/Users/me/dev/proj"


def test_list_cursor_editor_emits_one_per_workspace(tmp_path, monkeypatch):
    _seed_cursor_project(tmp_path, "Users-me-dev-projA", "/Users/me/dev/projA")
    _seed_cursor_project(tmp_path, "Users-me-dev-projB", "/Users/me/dev/projB")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [7001])
    sessions = list_cursor_editor_windows()
    assert {s.cwd for s in sessions} == {"/Users/me/dev/projA", "/Users/me/dev/projB"}


def test_list_cursor_editor_keeps_when_agent_hot_but_top_level_stale(tmp_path, monkeypatch):
    """Agents-only churn can leave project-dir top-level mtimes stale."""
    _seed_cursor_project(tmp_path, "Users-me-dev-proj", "/Users/me/dev/proj", fresh=False)
    transcripts = tmp_path / "Users-me-dev-proj" / "agent-transcripts" / "u"
    transcripts.mkdir(parents=True)
    (transcripts / "u.jsonl").write_text("{}")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [7001])
    sessions = list_cursor_editor_windows()
    assert len(sessions) == 1
    assert sessions[0].cwd == "/Users/me/dev/proj"


def test_cursor_workspace_cwd_slug_fallback_without_terminals(tmp_path, monkeypatch):
    name = "Users-me-dev-proj"
    proj = tmp_path / name
    transcripts = proj / "agent-transcripts" / "u"
    transcripts.mkdir(parents=True)
    (transcripts / "u.jsonl").write_text("{}")
    monkeypatch.setattr("overclocked.detectors._CURSOR_PROJECTS_DIR", tmp_path)
    assert _cursor_project_workspace_cwd(proj) == "/Users/me/dev/proj"


def test_merge_cursor_prefers_agent_for_same_cwd():
    ed = [Session(tool="cursor_editor", pid=101, cwd="/workspace")]
    ag = [Session(tool="cursor_agent", pid=102, cwd="/workspace")]
    merged = _merge_cursor_editor_and_agent(ed, ag)
    assert len(merged) == 1
    assert merged[0].tool == "cursor_agent"
    assert merged[0].pid == 102


# ── codex_app_session_is_active / list_codex_app_sessions ────────────────────


def test_codex_app_session_active_recent(tmp_path):
    f = tmp_path / "session.jsonl"
    f.write_text("{}")
    assert codex_app_session_is_active(f)


def test_codex_app_session_inactive_stale(tmp_path):
    import os

    f = tmp_path / "session.jsonl"
    f.write_text("{}")
    old = time.time() - 2000
    os.utime(f, (old, old))
    assert not codex_app_session_is_active(f)


def _iso_now_z() -> str:
    from datetime import datetime

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _make_codex_session(
    path: Path,
    cwd: str,
    originator: str = "Codex Desktop",
    *,
    timestamp: str | None = None,
) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"originator": originator, "cwd": cwd}
    ts = _iso_now_z() if timestamp is None else timestamp
    record = {"timestamp": ts, "type": "session_meta", "payload": payload}
    path.write_text(json.dumps(record) + "\n")


def _make_claude_session(
    path: Path,
    cwd: str,
    *,
    entrypoint: str = "claude-desktop",
    session_id: str = "session-1",
    timestamp: str | None = None,
) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    ts = _iso_now_z() if timestamp is None else timestamp
    record = {
        "timestamp": ts,
        "cwd": cwd,
        "entrypoint": entrypoint,
        "sessionId": session_id,
        "type": "assistant",
    }
    path.write_text(json.dumps(record) + "\n")


def test_list_codex_app_sessions_detects_desktop(tmp_path, monkeypatch):
    f = tmp_path / "2026" / "04" / "18" / "session-abc.jsonl"
    _make_codex_session(f, "/Users/me/proj")
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    sessions = list_codex_app_sessions()
    assert len(sessions) == 1
    assert sessions[0].cwd == "/Users/me/proj"
    assert sessions[0].tool == "codex"


def test_codex_session_meta_found_after_non_meta_preamble(tmp_path, monkeypatch):
    """session_meta may not be the first jsonl line."""
    import json

    f = tmp_path / "late-meta.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "timestamp": "2026-01-01T12:00:00.000Z",
        "type": "session_meta",
        "payload": {"originator": "Codex Desktop", "cwd": "/Users/me/proj"},
    }
    recent = json.dumps({"timestamp": _iso_now_z(), "type": "assistant", "payload": {}})
    f.write_text(
        json.dumps({"type": "event", "payload": {}})
        + "\n"
        + json.dumps(meta)
        + "\n"
        + recent
        + "\n"
    )
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    sessions = list_codex_app_sessions()
    assert len(sessions) == 1
    assert sessions[0].cwd == "/Users/me/proj"


def test_list_codex_app_sessions_skips_tui(tmp_path, monkeypatch):
    f = tmp_path / "session-tui.jsonl"
    _make_codex_session(f, "/Users/me/proj", originator="codex-tui")
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    sessions = list_codex_app_sessions()
    assert sessions == []


def test_list_codex_app_sessions_deduplicates_by_cwd(tmp_path, monkeypatch):
    import os

    f1 = tmp_path / "session-1.jsonl"
    f2 = tmp_path / "session-2.jsonl"
    _make_codex_session(f1, "/Users/me/proj")
    _make_codex_session(f2, "/Users/me/proj")
    os.utime(f1, (time.time(), time.time()))
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    sessions = list_codex_app_sessions()
    assert len(sessions) == 1


def test_list_codex_app_sessions_ignores_old(tmp_path, monkeypatch):
    import os

    f = tmp_path / "old-session.jsonl"
    _make_codex_session(f, "/Users/me/proj")
    old = time.time() - 2000
    os.utime(f, (old, old))
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    sessions = list_codex_app_sessions()
    assert sessions == []


def test_list_codex_app_sessions_none_cwd_distinct(tmp_path, monkeypatch):
    """Multiple sessions with no cwd must not collapse into one entry."""
    import json

    def _make_no_cwd(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "session_meta",
                    "payload": {"originator": "Codex Desktop", "cwd": None},
                }
            )
            + "\n"
        )

    f1 = tmp_path / "s1.jsonl"
    f2 = tmp_path / "s2.jsonl"
    _make_no_cwd(f1)
    _make_no_cwd(f2)
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    sessions = list_codex_app_sessions()
    assert len(sessions) == 2


# ── list_claude_app_sessions / list_claude_sessions ──────────────────────────


def test_list_claude_app_sessions_detects_recent_desktop_file(tmp_path, monkeypatch):
    f = tmp_path / "-Users-me-dev-proj" / "session-1.jsonl"
    _make_claude_session(f, "/Users/me/dev/proj")
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    sessions = list_claude_app_sessions()
    assert len(sessions) == 1
    assert sessions[0].cwd == "/Users/me/dev/proj"
    assert sessions[0].tool == "claude"


def test_list_claude_app_sessions_ignores_cli_file(tmp_path, monkeypatch):
    f = tmp_path / "-Users-me" / "cli-session.jsonl"
    _make_claude_session(f, "/Users/me", entrypoint="cli")
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    assert list_claude_app_sessions() == []


def test_list_claude_app_sessions_ignores_old_file(tmp_path, monkeypatch):
    import os

    f = tmp_path / "-Users-me-dev-proj" / "old-session.jsonl"
    _make_claude_session(f, "/Users/me/dev/proj")
    old = time.time() - 2000
    os.utime(f, (old, old))
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    assert list_claude_app_sessions() == []


def test_list_claude_app_sessions_ignores_subagent_files(tmp_path, monkeypatch):
    """Subagent jsonls inherit ``entrypoint=claude-desktop`` from their parent.

    They live at ``<project>/<sessionId>/subagents/agent-*.jsonl`` and must not
    be mistaken for top-level desktop sessions — the parent already covers them
    and rendering surfaces them as nested children.
    """
    parent_file = tmp_path / "-Users-me-dev-proj" / "session-1.jsonl"
    _make_claude_session(parent_file, "/Users/me/dev/proj", session_id="session-1")
    sub_file = tmp_path / "-Users-me-dev-proj" / "session-1" / "subagents" / "agent-abc.jsonl"
    _make_claude_session(sub_file, "/Users/me/dev/proj", session_id="session-1")
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    sessions = list_claude_app_sessions()
    assert len(sessions) == 1
    assert sessions[0].transcript_path == parent_file


def test_list_claude_app_sessions_counts_recent_files_not_helper_pids(tmp_path, monkeypatch):
    import os

    project_dir = tmp_path / "-Users-me-dev-proj"
    recent = project_dir / "recent.jsonl"
    old_1 = project_dir / "old-1.jsonl"
    old_2 = project_dir / "old-2.jsonl"
    _make_claude_session(recent, "/Users/me/dev/proj", session_id="recent")
    _make_claude_session(old_1, "/Users/me/dev/proj", session_id="old-1")
    _make_claude_session(old_2, "/Users/me/dev/proj", session_id="old-2")
    old = time.time() - 2000
    os.utime(old_1, (old, old))
    os.utime(old_2, (old, old))
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    sessions = list_claude_app_sessions()
    assert len(sessions) == 1
    assert sessions[0].cwd == "/Users/me/dev/proj"


def test_list_claude_sessions_tty_filter(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [1001, 1002])
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: pid == 1001)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr(
        "overclocked.detectors.claude_cli_session_is_active",
        lambda pid: pid == 1001,
    )
    monkeypatch.setattr("overclocked.detectors.list_claude_app_sessions", lambda: [])
    sessions = list_claude_sessions()
    assert len(sessions) == 1
    assert sessions[0].pid == 1001


def test_list_claude_sessions_excludes_ralph(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [1001])
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: True)
    monkeypatch.setattr("overclocked.detectors.list_claude_app_sessions", lambda: [])
    sessions = list_claude_sessions()
    assert sessions == []


def test_list_claude_tty_hides_when_project_stale(tmp_path, monkeypatch):
    """TTY Claude disappears once project activity is stale."""
    proj_dir = tmp_path / ".claude" / "projects" / "-Users-me-dev-proj"
    proj_dir.mkdir(parents=True)
    conv = proj_dir / "conversation.jsonl"
    conv.write_text("x")
    import os

    os.utime(conv, (time.time() - 2000, time.time() - 2000))

    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [3001])
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", proj_dir.parent)
    monkeypatch.setattr(
        "overclocked.detectors._resolve_cwd_cached",
        lambda pid: "/Users/me/dev/proj",
    )
    monkeypatch.setattr("overclocked.detectors.list_claude_app_sessions", lambda: [])
    sessions = list_claude_sessions()
    assert sessions == []


def test_list_claude_sessions_includes_recent_app_sessions(tmp_path, monkeypatch):
    f = tmp_path / "-Users-me-dev-proj" / "desktop-session.jsonl"
    _make_claude_session(f, "/Users/me/dev/proj")
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [])
    sessions = list_claude_sessions()
    assert len(sessions) == 1
    assert sessions[0].cwd == "/Users/me/dev/proj"


def test_list_claude_sessions_dedupes_desktop_tty_same_cwd(monkeypatch):
    """Desktop file-backed session and TTY helper same repo → one row (desktop)."""
    monkeypatch.setattr(
        "overclocked.detectors.list_claude_app_sessions",
        lambda: [Session(tool="claude", pid=500_001, cwd="/Users/me/proj")],
    )
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [777])
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors.claude_cli_session_is_active", lambda pid: True)
    monkeypatch.setattr(
        "overclocked.detectors.resolve_cwds_batch",
        lambda pids, **kw: {777: "/Users/me/proj"},
    )
    sessions = list_claude_sessions()
    assert len(sessions) == 1
    assert sessions[0].pid == 500_001
    assert sessions[0].cwd == "/Users/me/proj"


def test_list_claude_sessions_two_tty_same_cwd_without_desktop(monkeypatch):
    monkeypatch.setattr("overclocked.detectors.list_claude_app_sessions", lambda: [])
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [10, 11])
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors.claude_cli_session_is_active", lambda pid: True)
    monkeypatch.setattr(
        "overclocked.detectors.resolve_cwds_batch",
        lambda pids, **kw: {10: "/Users/me/repo", 11: "/Users/me/repo"},
    )
    sessions = list_claude_sessions()
    assert len(sessions) == 2
    assert {s.pid for s in sessions} == {10, 11}


def test_list_claude_sessions_keeps_tty_when_cwd_unresolved_desktop_present(monkeypatch):
    monkeypatch.setattr(
        "overclocked.detectors.list_claude_app_sessions",
        lambda: [Session(tool="claude", pid=800_000, cwd="/Users/me/proj")],
    )
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [9])
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors.claude_cli_session_is_active", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.resolve_cwds_batch", lambda pids, **kw: {9: None})
    sessions = list_claude_sessions()
    assert len(sessions) == 2
    by_pid = {s.pid: s for s in sessions}
    assert by_pid[800_000].cwd == "/Users/me/proj"
    assert by_pid[9].cwd is None


# ── subagent discovery ────────────────────────────────────────────────────────


def _make_subagent_jsonl(path: Path, *, parent_sid: str, agent_id: str) -> None:
    """Write a minimal subagent jsonl line at ``path``."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "user",
        "isSidechain": True,
        "agentId": agent_id,
        "sessionId": parent_sid,
        "timestamp": _iso_now_z(),
    }
    path.write_text(json.dumps(record) + "\n")


def test_list_claude_sessions_attaches_live_subagents(tmp_path, monkeypatch):
    """Parent desktop session with two recent subagent jsonls → two subagent rows."""
    parent_sid = "11111111-2222-3333-4444-555555555555"
    proj = tmp_path / "-Users-me-dev-proj"
    parent_jsonl = proj / f"{parent_sid}.jsonl"
    _make_claude_session(parent_jsonl, "/Users/me/dev/proj", session_id=parent_sid)
    sub_dir = proj / parent_sid / "subagents"
    _make_subagent_jsonl(sub_dir / "agent-aaa1.jsonl", parent_sid=parent_sid, agent_id="aaa1")
    _make_subagent_jsonl(sub_dir / "agent-bbb2.jsonl", parent_sid=parent_sid, agent_id="bbb2")

    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [])
    sessions = list_claude_sessions(Config())

    parents = [s for s in sessions if not s.is_subagent]
    children = [s for s in sessions if s.is_subagent]
    assert len(parents) == 1
    assert parents[0].session_id == parent_sid
    assert len(children) == 2
    assert {c.agent_id for c in children} == {"aaa1", "bbb2"}
    assert all(c.parent_session_id == parent_sid for c in children)
    assert all(c.cwd == "/Users/me/dev/proj" for c in children)
    assert all(c.synthetic for c in children)


def test_list_claude_sessions_filters_stale_subagents(tmp_path, monkeypatch):
    """Subagent jsonl with mtime outside the activity window is dropped."""
    import os

    parent_sid = "11111111-2222-3333-4444-555555555555"
    proj = tmp_path / "-Users-me-dev-proj"
    parent_jsonl = proj / f"{parent_sid}.jsonl"
    _make_claude_session(parent_jsonl, "/Users/me/dev/proj", session_id=parent_sid)
    sub_dir = proj / parent_sid / "subagents"
    fresh = sub_dir / "agent-fresh.jsonl"
    stale = sub_dir / "agent-stale.jsonl"
    _make_subagent_jsonl(fresh, parent_sid=parent_sid, agent_id="fresh")
    _make_subagent_jsonl(stale, parent_sid=parent_sid, agent_id="stale")
    old = time.time() - 2000
    os.utime(stale, (old, old))

    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [])
    sessions = list_claude_sessions(Config())

    children = [s for s in sessions if s.is_subagent]
    assert [c.agent_id for c in children] == ["fresh"]


def test_list_claude_sessions_drops_subagents_after_60s_quiet(tmp_path, monkeypatch):
    """A subagent silent for 60s is dropped, even though the parent's 5-min window persists.

    Verifies subagents disappear well before the parent activity window expires.
    """
    import os

    parent_sid = "22222222-3333-4444-5555-666666666666"
    proj = tmp_path / "-Users-me-dev-proj"
    _make_claude_session(proj / f"{parent_sid}.jsonl", "/Users/me/dev/proj", session_id=parent_sid)
    sub_dir = proj / parent_sid / "subagents"
    quiet = sub_dir / "agent-quiet.jsonl"
    _make_subagent_jsonl(quiet, parent_sid=parent_sid, agent_id="quiet")
    sixty_sec_ago = time.time() - 60
    os.utime(quiet, (sixty_sec_ago, sixty_sec_ago))

    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [])
    sessions = list_claude_sessions(Config())

    parents = [s for s in sessions if not s.is_subagent]
    children = [s for s in sessions if s.is_subagent]
    assert len(parents) == 1  # parent still shown (5-min window not yet exceeded)
    assert children == []  # subagent dropped after the tighter 30s window


def test_list_claude_sessions_finds_subagents_under_dotted_cwd(tmp_path, monkeypatch):
    """Worktree-style cwds (containing ``.``/``_``) resolve to the right project dir.

    Claude Code encodes the cwd by replacing ``/``, ``.``, and ``_`` with ``-``,
    so ``/Users/me/dev/x/.claude/wt`` lands at ``-Users-me-dev-x--claude-wt``.
    Earlier code only replaced ``/`` and silently failed for these paths.
    """
    parent_sid = "abcdef00-1111-2222-3333-444444444444"
    proj = tmp_path / "-Users-me-dev-x--claude-wt"
    parent_jsonl = proj / f"{parent_sid}.jsonl"
    _make_claude_session(parent_jsonl, "/Users/me/dev/x/.claude/wt", session_id=parent_sid)
    sub_dir = proj / parent_sid / "subagents"
    _make_subagent_jsonl(sub_dir / "agent-aaa1.jsonl", parent_sid=parent_sid, agent_id="aaa1")

    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [])
    sessions = list_claude_sessions(Config())
    children = [s for s in sessions if s.is_subagent]
    assert [c.agent_id for c in children] == ["aaa1"]


def test_list_claude_sessions_show_subagents_false_omits_children(tmp_path, monkeypatch):
    """show_subagents=False suppresses subagent rows."""
    parent_sid = "deadbeef-0000-0000-0000-000000000000"
    proj = tmp_path / "-Users-me-dev-proj"
    _make_claude_session(proj / f"{parent_sid}.jsonl", "/Users/me/dev/proj", session_id=parent_sid)
    sub_dir = proj / parent_sid / "subagents"
    _make_subagent_jsonl(sub_dir / "agent-x.jsonl", parent_sid=parent_sid, agent_id="x")

    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [])
    sessions = list_claude_sessions(Config(show_subagents=False))

    assert all(not s.is_subagent for s in sessions)


# ── _claude_pgrep_all (single call per tick) ──────────────────────────────────


def test_claude_pgrep_all_single_call(monkeypatch):
    """_claude_pgrep_all issues exactly one _safe_check_output call."""
    call_count = {"n": 0}

    def fake_safe(args, **kw):
        if args[0] == "pgrep":
            call_count["n"] += 1
        return "1234\n5678\n"

    monkeypatch.setattr("overclocked.detectors._safe_check_output", fake_safe)
    result = _claude_pgrep_all()
    assert call_count["n"] == 1
    assert result == [1234, 5678]


# ── list_codex_sessions ───────────────────────────────────────────────────────


def test_list_codex_excludes_daemon(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9001])
    monkeypatch.setattr("overclocked.detectors._argv", lambda pid: "codex-companion --daemon")
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: 500)
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    sessions = list_codex_sessions()
    assert sessions == []


def test_list_codex_excludes_launchd_child(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9002])
    monkeypatch.setattr("overclocked.detectors._argv", lambda pid: "codex run")
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: 1)
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    sessions = list_codex_sessions()
    assert sessions == []


def test_list_codex_valid_session(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9003])
    monkeypatch.setattr("overclocked.detectors._argv", lambda pid: "codex run")
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: 500)
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors.codex_cli_session_is_active", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors._resolve_cwd_cached", lambda pid: None)
    sessions = list_codex_sessions()
    assert len(sessions) == 1
    assert sessions[0].tool == "codex"


def test_list_codex_dedupes_wrapper_and_child(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9006, 9007])
    monkeypatch.setattr(
        "overclocked.detectors._argv",
        lambda pid: "node /Users/me/.npm-global/bin/codex" if pid == 9006 else "/vendor/codex",
    )
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: {9006: 500, 9007: 9006}.get(pid))
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors.codex_cli_session_is_active", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors._resolve_cwd_cached", lambda pid: "/Users/me")
    sessions = list_codex_sessions()
    assert [s.pid for s in sessions] == [9007]


def test_list_codex_cli_drops_stale_session_file(tmp_path, monkeypatch):
    f = tmp_path / "cli.jsonl"
    _make_codex_session(f, "/Users/me/proj", originator="codex-tui")
    import os

    os.utime(f, (time.time() - 2000, time.time() - 2000))

    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9004])
    monkeypatch.setattr("overclocked.detectors._argv", lambda pid: "codex run")
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: 500)
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._resolve_cwd_cached", lambda pid: "/Users/me/proj")
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 0.0)
    assert list_codex_sessions() == []


def test_list_codex_cli_keeps_recent_session_file(tmp_path, monkeypatch):
    f = tmp_path / "cli.jsonl"
    _make_codex_session(f, "/Users/me/proj", originator="codex-tui")

    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9005])
    monkeypatch.setattr("overclocked.detectors._argv", lambda pid: "codex run")
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: 500)
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._resolve_cwd_cached", lambda pid: "/Users/me/proj")
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 0.0)
    sessions = list_codex_sessions()
    assert len(sessions) == 1
    assert sessions[0].pid == 9005


def test_list_codex_cli_hides_without_activity(monkeypatch):
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9008])
    monkeypatch.setattr("overclocked.detectors._argv", lambda pid: "codex run")
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: 500)
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors._resolve_cwd_cached", lambda pid: None)
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 0.0)
    assert list_codex_sessions() == []


def test_codex_app_session_inactive_fresh_mtime_stale_transcript(tmp_path):
    import os

    f = tmp_path / "session.jsonl"
    _make_codex_session(f, "/Users/me/proj", timestamp="2020-01-01T00:00:00Z")
    os.utime(f, (time.time(), time.time()))
    assert not codex_app_session_is_active(f)


def test_list_claude_app_sessions_skips_stale_transcript(tmp_path, monkeypatch):
    import os

    f = tmp_path / "-Users-me-dev-proj" / "session-1.jsonl"
    _make_claude_session(f, "/Users/me/dev/proj", timestamp="2020-01-01T00:00:00Z")
    os.utime(f, (time.time(), time.time()))
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", tmp_path)
    assert list_claude_app_sessions() == []


def test_list_codex_cli_hides_recent_file_stale_transcript(tmp_path, monkeypatch):
    import os

    f = tmp_path / "cli.jsonl"
    _make_codex_session(
        f,
        "/Users/me/proj",
        originator="codex-tui",
        timestamp="2020-01-01T00:00:00Z",
    )
    os.utime(f, (time.time(), time.time()))
    monkeypatch.setattr("overclocked.detectors._pgrep", lambda p: [9010])
    monkeypatch.setattr("overclocked.detectors._argv", lambda pid: "codex run")
    monkeypatch.setattr("overclocked.detectors._ppid", lambda pid: 500)
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors._CODEX_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr("overclocked.detectors._resolve_cwd_cached", lambda pid: "/Users/me/proj")
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 0.0)
    assert list_codex_sessions() == []


def test_claude_cli_inactive_stale_agent_transcript(tmp_path, monkeypatch):
    import json

    proj_dir = tmp_path / ".claude" / "projects" / "-Users-me-dev-proj"
    proj_dir.mkdir(parents=True)
    agent = proj_dir / "agent-test.jsonl"
    record = {"timestamp": "2020-01-01T00:00:00Z", "type": "user", "message": {}}
    agent.write_text(json.dumps(record) + "\n")
    fresh = proj_dir / "conversation.jsonl"
    fresh.write_text("x")
    monkeypatch.setattr("overclocked.detectors._claude_pgrep_all", lambda: [3002])
    monkeypatch.setattr("overclocked.detectors._has_tty", lambda pid: True)
    monkeypatch.setattr("overclocked.detectors.is_descendant_of", lambda pid, names: False)
    monkeypatch.setattr("overclocked.detectors._CLAUDE_PROJECTS_DIR", proj_dir.parent)
    monkeypatch.setattr(
        "overclocked.detectors._resolve_cwd_cached",
        lambda pid: "/Users/me/dev/proj",
    )
    monkeypatch.setattr("overclocked.detectors._cpu_percent", lambda pid: 0.0)
    monkeypatch.setattr("overclocked.detectors.list_claude_app_sessions", lambda: [])
    assert list_claude_sessions() == []


# ── tick / debounce ───────────────────────────────────────────────────────────


def test_tick_first_tick_returns_nothing(monkeypatch):
    monkeypatch.setattr(
        "overclocked.detectors.list_all_sessions",
        lambda config=None: [Session(tool="claude", pid=1)],
    )
    first = tick(Config())
    assert stable_sessions_from_keys(first, frozenset()) == []


def test_tick_two_ticks_confirms(monkeypatch):
    monkeypatch.setattr(
        "overclocked.detectors.list_all_sessions",
        lambda config=None: [Session(tool="claude", pid=1)],
    )
    k1 = raw_session_keys(tick(Config()))
    sessions = stable_sessions_from_keys(tick(Config()), k1)
    assert len(sessions) == 1
    assert sessions[0].tool == "claude"


def test_tick_flicker_not_propagated(monkeypatch):
    """A session appearing in tick 1 but not tick 2 is not emitted after tick 2."""
    call_count = {"n": 0}

    def fake_list(config=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return [Session(tool="claude", pid=1)]
        return []

    monkeypatch.setattr("overclocked.detectors.list_all_sessions", fake_list)
    k1 = raw_session_keys(tick(Config()))
    assert stable_sessions_from_keys(tick(Config()), k1) == []


def test_tick_stable_addition(monkeypatch):
    """Session seen in tick N and N+1 but not N-1 is emitted after tick N+1."""
    call_count = {"n": 0}

    def fake_list(config=None):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            return [Session(tool="codex", pid=77)]
        return []

    monkeypatch.setattr("overclocked.detectors.list_all_sessions", fake_list)
    k1 = raw_session_keys(tick(Config()))
    second = tick(Config())
    assert stable_sessions_from_keys(second, k1) == []
    k2 = raw_session_keys(second)
    assert len(stable_sessions_from_keys(tick(Config()), k2)) == 1


# ── session status (abtop-style) ──────────────────────────────────────────────


def test_has_active_descendant_detects_hot_child(monkeypatch):
    import overclocked.detectors as d

    monkeypatch.setattr(
        d,
        "_ps_table",
        {
            100: PsRow(ppid=1, tty="?", pcpu=0.0, command="parent"),
            101: PsRow(ppid=100, tty="?", pcpu=6.0, command="child"),
        },
    )
    assert has_active_descendant(100, 5.0) is True


def test_has_active_descendant_respects_threshold(monkeypatch):
    import overclocked.detectors as d

    monkeypatch.setattr(
        d,
        "_ps_table",
        {
            100: PsRow(ppid=1, tty="?", pcpu=0.0, command="parent"),
            101: PsRow(ppid=100, tty="?", pcpu=2.0, command="child"),
        },
    )
    assert has_active_descendant(100, 5.0) is False


def test_working_or_waiting_recency(monkeypatch):
    import overclocked.detectors as d

    monkeypatch.setattr(d, "_cpu_percent", lambda pid: 0.0)
    monkeypatch.setattr(d, "has_active_descendant", lambda pid, t: False)
    assert d._working_or_waiting_from_signals(1, 100.0, now=120.0) == "working"
    assert d._working_or_waiting_from_signals(1, 10.0, now=120.0) == "waiting"


def test_working_or_waiting_parent_cpu(monkeypatch):
    import overclocked.detectors as d

    monkeypatch.setattr(d, "_cpu_percent", lambda pid: 2.0)
    monkeypatch.setattr(d, "has_active_descendant", lambda pid, t: False)
    assert d._working_or_waiting_from_signals(7, None, now=1000.0) == "working"


def test_codex_cli_status_exec_done(monkeypatch, tmp_path):
    import overclocked.detectors as d

    rollout = tmp_path / "rollout-1.jsonl"
    rollout.write_text(
        '{"type":"event_msg","timestamp":"2026-01-01T00:00:00Z",'
        '"payload":{"type":"task_complete"}}\n'
    )
    data = CodexTickData(
        frozenset({"/Users/me/proj"}),
        {"/Users/me/proj": rollout},
        [],
    )
    monkeypatch.setattr(d, "_ensure_codex_tick_data", lambda: data)
    monkeypatch.setattr(d, "_cpu_percent", lambda pid: 0.0)
    monkeypatch.setattr(d, "has_active_descendant", lambda pid, t: False)
    assert d._codex_cli_session_status(42, "/Users/me/proj", "/usr/bin/codex exec x") == "done"


def test_codex_cli_status_waiting_stale_transcript(monkeypatch, tmp_path):
    import overclocked.detectors as d

    rollout = tmp_path / "rollout-2.jsonl"
    rollout.write_text(
        '{"type":"session_meta","timestamp":"2020-01-01T00:00:00Z","payload":'
        '{"id":"s","cwd":"/Users/me/p"}}\n'
    )
    data = CodexTickData(
        frozenset({"/Users/me/p"}),
        {"/Users/me/p": rollout},
        [],
    )
    monkeypatch.setattr(d, "_ensure_codex_tick_data", lambda: data)
    monkeypatch.setattr(d, "_cpu_percent", lambda pid: 0.0)
    monkeypatch.setattr(d, "has_active_descendant", lambda pid, t: False)
    assert d._codex_cli_session_status(43, "/Users/me/p", "codex") == "waiting"


def test_cursor_coarse_status_recent_terminal(tmp_path):
    import os

    import overclocked.detectors as d

    proj = tmp_path / "Users-me-dev-proj"
    (proj / "terminals").mkdir(parents=True)
    t = proj / "terminals" / "1.txt"
    t.write_text("---\ncwd: /Users/me/dev/proj\n---\n")
    now = time.time()
    os.utime(t, (now, now))
    assert d._cursor_coarse_status(proj) == "working"


# ── session metrics enrichment ────────────────────────────────────────────────


def test_enrich_session_metrics_skipped_when_disabled(monkeypatch, tmp_path):
    import overclocked.detectors as d

    def boom(*_a, **_kw):
        raise AssertionError("parse should not run when session_metrics is false")

    monkeypatch.setattr(d, "parse_claude_jsonl_tail", boom)
    s = Session(
        tool="claude",
        pid=1,
        cwd="/Users/me/p",
        project="p",
        transcript_path=tmp_path / "x.jsonl",
    )
    d._enrich_session_metrics([s], Config(session_metrics=False))


def test_enrich_session_metrics_clears_for_redacted_project():
    import overclocked.detectors as d

    s = Session(
        tool="claude",
        pid=1,
        cwd="/Users/me/x",
        project="redacted",
        model="m",
        input_tokens=9,
    )
    d._enrich_session_metrics([s], Config(session_metrics=True))
    assert s.model is None
    assert s.input_tokens is None


def test_enrich_session_metrics_clears_for_redacted_cwd():
    import overclocked.detectors as d

    home = Path.home()
    cwd = str(home / "clients" / "secret")
    s = Session(
        tool="codex",
        pid=1,
        cwd=cwd,
        project="proj",
        model="gpt",
        output_tokens=1,
    )
    d._enrich_session_metrics([s], Config(redact_paths=["~/clients/"], session_metrics=True))
    assert s.model is None
    assert s.output_tokens is None


def test_enrich_session_metrics_fills_from_transcript(monkeypatch, tmp_path):
    import overclocked.detectors as d
    from overclocked.transcript_metrics import UsageSnapshot

    p = tmp_path / "sess.jsonl"
    p.write_text("{}\n")

    def fake_parse(path: Path):
        assert path == p
        return UsageSnapshot(
            model="claude-3-opus",
            input_tokens=3,
            output_tokens=4,
            cache_read=0,
            cache_create=0,
        )

    monkeypatch.setattr(d, "parse_claude_jsonl_tail", fake_parse)
    s = Session(tool="claude", pid=1, cwd="/a", project="p", transcript_path=p)
    d._enrich_session_metrics([s], Config(session_metrics=True))
    assert s.model == "claude-3-opus"
    assert s.input_tokens == 3
    assert s.output_tokens == 4


def test_enrich_claude_tty_matches_abtop_per_session_transcript(tmp_path, monkeypatch):
    """TTY sessions in the same cwd must not share one project-dir parse (same token totals)."""
    import overclocked.detectors as d

    cfg_root = tmp_path / "claude"
    (cfg_root / "sessions").mkdir(parents=True)
    (cfg_root / "projects").mkdir(parents=True)
    cwd = "/Users/me/repo"
    enc = d._encode_cwd_for_claude_projects(cwd)
    proj_dir = cfg_root / "projects" / enc
    proj_dir.mkdir(parents=True)

    (cfg_root / "sessions" / "100.json").write_text(
        json.dumps({"pid": 100, "sessionId": "sess-a", "cwd": cwd, "startedAt": 1}),
        encoding="utf-8",
    )
    (cfg_root / "sessions" / "200.json").write_text(
        json.dumps({"pid": 200, "sessionId": "sess-b", "cwd": cwd, "startedAt": 1}),
        encoding="utf-8",
    )

    def assistant_line(inp: int) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "message": {
                    "model": "claude-sonnet-4",
                    "usage": {
                        "input_tokens": inp,
                        "output_tokens": 1,
                        "cache_read_input_tokens": 0,
                        "cache_creation_input_tokens": 0,
                    },
                },
            }
        )

    (proj_dir / "sess-a.jsonl").write_text(assistant_line(10) + "\n", encoding="utf-8")
    (proj_dir / "sess-b.jsonl").write_text(assistant_line(5000) + "\n", encoding="utf-8")

    monkeypatch.setattr(d, "_claude_config_base", lambda: cfg_root)

    a = Session(tool="claude", pid=100, cwd=cwd, project="repo")
    b = Session(tool="claude", pid=200, cwd=cwd, project="repo")
    d._enrich_session_metrics([a, b], Config(session_metrics=True))
    assert a.input_tokens == 10
    assert b.input_tokens == 5000
