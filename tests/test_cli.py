"""Integration-style tests for the CLI exception guard."""

from __future__ import annotations

import signal
import sqlite3

import pytest

from overclocked.cli import main
from overclocked.detectors import Session


def test_cli_guard_exits_zero_on_storage_error(tmp_path, monkeypatch, capsys):
    """cli.main() exits 0 with 👾 ! on stdout when storage raises."""
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))

    def raise_op_error(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("overclocked.cli.connect", raise_op_error)

    # Should not raise; the guard catches the exception
    main(["--once"])

    captured = capsys.readouterr()
    assert "👾 !" in captured.out
    assert "OperationalError" in captured.err or "database is locked" in captured.err


def test_cli_guard_writes_error_log(tmp_path, monkeypatch):
    """cli.main() appends a timestamped entry to ~/.overclocked/error.log."""
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))

    def raise_error(*a, **kw):
        raise RuntimeError("test error")

    monkeypatch.setattr("overclocked.cli.connect", raise_error)
    main(["--once"])

    error_log = tmp_path / "error.log"
    assert error_log.exists()
    content = error_log.read_text()
    assert "RuntimeError" in content or "test error" in content


def test_cli_dump_state_bypasses_guard(tmp_path, monkeypatch):
    """--dump-state lets exceptions propagate (no guard)."""
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))

    def boom(config):
        raise RuntimeError("boom")

    monkeypatch.setattr("overclocked.cli.tick", boom)
    with pytest.raises(RuntimeError, match="boom"):
        main(["--dump-state"])


def test_cli_dump_state_uses_active_schema(monkeypatch, capsys):
    sessions = [Session(tool="claude", pid=1, cwd="/dev/proj", project="proj")]
    monkeypatch.setattr("overclocked.cli.tick", lambda config: sessions)
    main(["--dump-state"])
    captured = capsys.readouterr()
    assert '"active": 1' in captured.out
    assert '"loaded"' not in captured.out
    assert '"hot"' not in captured.out


def test_cli_dump_state_includes_session_metrics(monkeypatch, capsys):
    sessions = [
        Session(
            tool="claude",
            pid=1,
            cwd="/dev/proj",
            project="proj",
            model="claude-opus",
            input_tokens=8,
            output_tokens=2,
            cache_read=100,
        ),
    ]
    monkeypatch.setattr("overclocked.cli.tick", lambda config: sessions)
    main(["--dump-state"])
    out = capsys.readouterr().out
    assert '"model": "claude-opus"' in out
    assert '"input_tokens": 8' in out
    assert '"cache_read": 100' in out


def test_cli_demo_is_deterministic_and_skips_local_detection(monkeypatch, capsys):
    def should_not_detect(config):  # noqa: ARG001
        raise AssertionError("demo should not inspect local sessions")

    def should_not_connect():
        raise AssertionError("demo should not write runtime history")

    monkeypatch.setattr("overclocked.cli.tick", should_not_detect)
    monkeypatch.setattr("overclocked.cli.connect", should_not_connect)

    main(["--demo"])

    out = capsys.readouterr().out
    assert "👾  3" in out
    assert "Claude Code" in out
    assert "Cursor" in out
    assert "Codex" in out
    assert "/demo/" not in out


def test_cli_rejects_non_positive_stream_interval(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--stream", "--interval", "0"])

    assert excinfo.value.code == 2
    assert "must be a finite number greater than 0" in capsys.readouterr().err


def test_cli_rejects_non_finite_stream_interval(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["--stream", "--interval", "nan"])

    assert excinfo.value.code == 2
    assert "must be a finite number greater than 0" in capsys.readouterr().err


# ─── --stream mode ──────────────────────────────────────────────────────────


def _install_stream_harness(monkeypatch, render_side_effect, stop_after_renders=3):
    """Wire up `_run_stream` to exit cleanly after N renders.

    Captures the SIGTERM handler the loop installs and triggers it from
    `_render_once` once `stop_after_renders` renders have completed. Stubs
    `connect`, `time.sleep`, and the persistence helpers so no real I/O
    happens and the test runs in well under a second.
    """
    monkeypatch.setattr("overclocked.cli.connect", lambda: object())
    monkeypatch.setattr("overclocked.cli.load_raw_session_keys", lambda: None)
    monkeypatch.setattr("overclocked.cli.save_raw_session_keys", lambda keys: None)
    monkeypatch.setattr("overclocked.cli.contextlib.closing", lambda obj: _NoOpCm(obj))

    captured = {"sigterm": None, "renders": 0}

    def fake_signal(sig, handler):
        if sig == signal.SIGTERM:
            captured["sigterm"] = handler
        return None

    monkeypatch.setattr("overclocked.cli.signal.signal", fake_signal)

    def fake_render(config, conn, prev):
        captured["renders"] += 1
        result = render_side_effect(captured["renders"], prev)
        if captured["renders"] >= stop_after_renders and captured["sigterm"] is not None:
            captured["sigterm"](signal.SIGTERM, None)
        return result

    monkeypatch.setattr("overclocked.cli._render_once", fake_render)
    monkeypatch.setattr("overclocked.cli.time.sleep", lambda _s: None)
    return captured


class _NoOpCm:
    def __init__(self, obj):
        self._obj = obj

    def __enter__(self):
        return self._obj

    def __exit__(self, *a):
        return False


def test_stream_emits_separator_between_renders(monkeypatch, capsys):
    def render(n, prev):
        return (f"👾 {n}\n---\nfoo", frozenset({("claude", n)}), [])

    captured = _install_stream_harness(monkeypatch, render, stop_after_renders=3)

    main(["--stream", "--interval", "0.001"])

    out = capsys.readouterr().out
    assert out.count("~~~") == 3
    assert out.count("👾") == 3
    assert "👾 1" in out and "👾 2" in out and "👾 3" in out
    assert captured["renders"] == 3


def test_stream_survives_render_exception(monkeypatch, capsys):
    def render(n, prev):
        if n == 2:
            raise RuntimeError("transient detector hiccup")
        return (f"👾 {n}\n---\nfoo", frozenset(), [])

    _install_stream_harness(monkeypatch, render, stop_after_renders=3)

    main(["--stream", "--interval", "0.001"])

    out = capsys.readouterr().out
    assert "👾 1" in out
    assert "👾 !" in out
    assert "transient detector hiccup" in out
    assert "👾 3" in out
    assert out.count("~~~") == 3


def test_stream_persists_last_keys_on_shutdown(monkeypatch, capsys):
    saved = {"keys": None}

    def render(n, prev):
        return (f"👾 {n}\n---\nfoo", frozenset({("codex", n)}), [])

    _install_stream_harness(monkeypatch, render, stop_after_renders=2)
    monkeypatch.setattr(
        "overclocked.cli.save_raw_session_keys",
        lambda keys: saved.update(keys=keys),
    )

    main(["--stream", "--interval", "0.001"])

    capsys.readouterr()
    assert saved["keys"] == frozenset({("codex", 2)})


def test_stream_exits_when_stdout_is_not_writable(monkeypatch):
    saved = {"keys": None}
    renders = {"count": 0}

    monkeypatch.setattr("overclocked.cli.connect", lambda: object())
    monkeypatch.setattr("overclocked.cli.load_raw_session_keys", lambda: frozenset({("claude", 7)}))
    monkeypatch.setattr(
        "overclocked.cli.save_raw_session_keys",
        lambda keys: saved.update(keys=keys),
    )
    monkeypatch.setattr("overclocked.cli.contextlib.closing", lambda obj: _NoOpCm(obj))
    monkeypatch.setattr("overclocked.cli.signal.signal", lambda *a, **kw: None)
    monkeypatch.setattr("overclocked.cli.select.select", lambda *a, **kw: ([], [], []))
    monkeypatch.setattr("overclocked.cli.os.getppid", lambda: 4242)

    def fail_render(*args, **kwargs):
        renders["count"] += 1
        raise AssertionError("_render_once should not run when stdout is stalled")

    monkeypatch.setattr("overclocked.cli._render_once", fail_render)

    class FakeStdout:
        def fileno(self):
            return 1

        def write(self, _data):
            raise AssertionError("write should not run when stdout is stalled")

        def flush(self):
            raise AssertionError("flush should not run when stdout is stalled")

    monkeypatch.setattr("overclocked.cli.sys.stdout", FakeStdout())

    main(["--stream", "--interval", "0.001"])

    assert renders["count"] == 0
    assert saved["keys"] == frozenset({("claude", 7)})


def test_stream_exits_cleanly_on_broken_pipe(monkeypatch):
    saved = {"keys": None}

    monkeypatch.setattr("overclocked.cli.connect", lambda: object())
    monkeypatch.setattr("overclocked.cli.load_raw_session_keys", lambda: None)
    monkeypatch.setattr(
        "overclocked.cli.save_raw_session_keys",
        lambda keys: saved.update(keys=keys),
    )
    monkeypatch.setattr("overclocked.cli.contextlib.closing", lambda obj: _NoOpCm(obj))
    monkeypatch.setattr("overclocked.cli.signal.signal", lambda *a, **kw: None)
    monkeypatch.setattr("overclocked.cli.select.select", lambda *a, **kw: ([], [1], []))
    monkeypatch.setattr("overclocked.cli.os.getppid", lambda: 4242)
    monkeypatch.setattr(
        "overclocked.cli._render_once",
        lambda config, conn, prev: ("👾 1\n---\nfoo", frozenset({("claude", 1)}), []),
    )

    class FakeStdout:
        def fileno(self):
            return 1

        def write(self, _data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            raise AssertionError("flush should not run after a broken pipe on write")

    monkeypatch.setattr("overclocked.cli.sys.stdout", FakeStdout())

    main(["--stream", "--interval", "0.001"])

    assert saved["keys"] == frozenset({("claude", 1)})
