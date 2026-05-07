"""Tests for the SwiftBar streamable wrapper script."""

from __future__ import annotations

import runpy
import subprocess
from pathlib import Path
from typing import TextIO

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "overclocked.py"


class FakeProcess:
    def __init__(self, lines: list[str], returncode: int) -> None:
        self.stdout = lines
        self.returncode = returncode

    def __enter__(self) -> FakeProcess:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def wait(self) -> int:
        return self.returncode


def _run_wrapper() -> None:
    runpy.run_path(str(SCRIPT), run_name="__main__")


def test_wrapper_missing_binary_emits_not_found(monkeypatch, capsys):
    def missing(*args: object, **kwargs: object) -> FakeProcess:
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "Popen", missing)
    _run_wrapper()
    out = capsys.readouterr().out
    assert "overclocked not found" in out
    assert "~~~" in out


def test_wrapper_clean_stream_forwards_output(monkeypatch, capsys):
    def popen(*args: object, **kwargs: object) -> FakeProcess:
        return FakeProcess(["line 1\n", "line 2\n"], 0)

    monkeypatch.setattr(subprocess, "Popen", popen)
    _run_wrapper()
    assert capsys.readouterr().out == "line 1\nline 2\n"


def test_wrapper_nonzero_without_output_emits_error_frame(monkeypatch, capsys):
    def popen(*args: object, **kwargs: object) -> FakeProcess:
        stderr = kwargs["stderr"]
        assert hasattr(stderr, "write")
        stderr.write("database is locked\n")
        return FakeProcess([], 2)

    monkeypatch.setattr(subprocess, "Popen", popen)
    _run_wrapper()
    out = capsys.readouterr().out
    assert "👾 !" in out
    assert "overclocked exited with code 2" in out
    assert "database is locked" in out
    assert "~~~" in out


def test_wrapper_nonzero_after_output_appends_error_frame(monkeypatch, capsys):
    def popen(*args: object, **kwargs: object) -> FakeProcess:
        stderr: TextIO = kwargs["stderr"]
        stderr.write("fatal stream error\n")
        return FakeProcess(["👾 1\n", "---\n"], 1)

    monkeypatch.setattr(subprocess, "Popen", popen)
    _run_wrapper()
    out = capsys.readouterr().out
    assert out.startswith("👾 1\n---\n")
    assert "overclocked exited with code 1" in out
    assert "fatal stream error" in out
