from __future__ import annotations

from overclocked.runtime_home import runtime_home


def test_runtime_home_uses_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", str(tmp_path))
    assert runtime_home() == tmp_path


def test_runtime_home_expands_user_in_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("OVERCLOCKED_HOME", "~/.cache/overclocked")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert runtime_home() == tmp_path / ".cache" / "overclocked"


def test_runtime_home_default_under_user_home(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERCLOCKED_HOME", raising=False)
    monkeypatch.setattr("overclocked.runtime_home.Path.home", lambda: tmp_path)
    assert runtime_home() == tmp_path / ".overclocked"
