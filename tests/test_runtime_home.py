from __future__ import annotations

import sqlite3

from overclocked.runtime_home import runtime_home
from overclocked.storage import connect


def test_runtime_home_uses_overclocked_env(tmp_path, monkeypatch):
    overclocked_home = tmp_path / "canonical"
    monkeypatch.setenv("OVERCLOCKED_HOME", str(overclocked_home))
    assert runtime_home() == overclocked_home


def test_runtime_home_defaults_to_dot_overclocked(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERCLOCKED_HOME", raising=False)
    monkeypatch.setattr("overclocked.runtime_home.Path.home", lambda: tmp_path)
    assert runtime_home() == tmp_path / ".overclocked"


def test_connect_uses_canonical_history(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERCLOCKED_HOME", raising=False)
    monkeypatch.setattr("overclocked.runtime_home.Path.home", lambda: tmp_path)

    canonical_home = tmp_path / ".overclocked"
    canonical_home.mkdir(exist_ok=True)
    db = sqlite3.connect(canonical_home / "history.db")
    db.execute(
        "CREATE TABLE snapshots (ts INTEGER PRIMARY KEY, active INTEGER NOT NULL, by_tool_json TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, tool TEXT NOT NULL, project TEXT, started_at INTEGER NOT NULL, ended_at INTEGER, pid INTEGER, session_key TEXT)"
    )
    db.execute("INSERT INTO snapshots (ts, active, by_tool_json) VALUES (1, 9, '{}')")
    db.execute("PRAGMA user_version = 3")
    db.commit()
    db.close()

    conn = connect()
    row = conn.execute("SELECT active FROM snapshots WHERE ts = 1").fetchone()
    assert row["active"] == 9
    conn.close()
