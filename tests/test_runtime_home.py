from __future__ import annotations

import sqlite3

from overclocked.runtime_home import runtime_home
from overclocked.storage import connect

_SNAPSHOTS_SCHEMA = (
    "CREATE TABLE snapshots ("
    "ts INTEGER PRIMARY KEY, "
    "active INTEGER NOT NULL, "
    "by_tool_json TEXT NOT NULL)"
)

_SESSIONS_SCHEMA = (
    "CREATE TABLE sessions ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "tool TEXT NOT NULL, "
    "project TEXT, "
    "started_at INTEGER NOT NULL, "
    "ended_at INTEGER, "
    "pid INTEGER, "
    "session_key TEXT)"
)


def test_runtime_home_prefers_overclocked_env(tmp_path, monkeypatch):
    overclocked_home = tmp_path / "canonical"
    quorum_home = tmp_path / "legacy"
    monkeypatch.setenv("OVERCLOCKED_HOME", str(overclocked_home))
    monkeypatch.setenv("QUORUM_HOME", str(quorum_home))
    assert runtime_home() == overclocked_home


def test_runtime_home_uses_quorum_env_as_legacy_fallback(tmp_path, monkeypatch):
    quorum_home = tmp_path / "legacy"
    monkeypatch.delenv("OVERCLOCKED_HOME", raising=False)
    monkeypatch.setenv("QUORUM_HOME", str(quorum_home))
    assert runtime_home() == quorum_home


def test_runtime_home_adopts_default_legacy_files(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERCLOCKED_HOME", raising=False)
    monkeypatch.delenv("QUORUM_HOME", raising=False)
    monkeypatch.setattr("overclocked.runtime_home.Path.home", lambda: tmp_path)

    legacy_home = tmp_path / ".quorum"
    legacy_home.mkdir()
    for name in ("config.toml", "history.db", "history.db-wal", "history.db-shm", "error.log"):
        (legacy_home / name).write_text(name)

    home = runtime_home()

    assert home == tmp_path / ".overclocked"
    for name in ("config.toml", "history.db", "history.db-wal", "history.db-shm", "error.log"):
        assert (home / name).exists()
        assert not (legacy_home / name).exists()


def test_connect_preserves_legacy_history_without_manual_copy(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERCLOCKED_HOME", raising=False)
    monkeypatch.delenv("QUORUM_HOME", raising=False)
    monkeypatch.setattr("overclocked.runtime_home.Path.home", lambda: tmp_path)

    legacy_home = tmp_path / ".quorum"
    legacy_home.mkdir()
    legacy_db = legacy_home / "history.db"
    conn = sqlite3.connect(legacy_db)
    conn.execute(_SNAPSHOTS_SCHEMA)
    conn.execute(_SESSIONS_SCHEMA)
    conn.execute("INSERT INTO snapshots (ts, active, by_tool_json) VALUES (1, 2, '{}')")
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    conn.close()

    migrated = connect()
    row = migrated.execute("SELECT active FROM snapshots WHERE ts = 1").fetchone()
    assert row["active"] == 2
    assert (tmp_path / ".overclocked" / "history.db").exists()
    migrated.close()


def test_connect_prefers_canonical_history_when_both_homes_exist(tmp_path, monkeypatch):
    monkeypatch.delenv("OVERCLOCKED_HOME", raising=False)
    monkeypatch.delenv("QUORUM_HOME", raising=False)
    monkeypatch.setattr("overclocked.runtime_home.Path.home", lambda: tmp_path)

    canonical_home = tmp_path / ".overclocked"
    canonical_home.mkdir(exist_ok=True)
    canonical_db = sqlite3.connect(canonical_home / "history.db")
    canonical_db.execute(_SNAPSHOTS_SCHEMA)
    canonical_db.execute(_SESSIONS_SCHEMA)
    canonical_db.execute("INSERT INTO snapshots (ts, active, by_tool_json) VALUES (1, 9, '{}')")
    canonical_db.execute("PRAGMA user_version = 3")
    canonical_db.commit()
    canonical_db.close()

    legacy_home = tmp_path / ".quorum"
    legacy_home.mkdir()
    legacy_db = sqlite3.connect(legacy_home / "history.db")
    legacy_db.execute(_SNAPSHOTS_SCHEMA)
    legacy_db.execute(_SESSIONS_SCHEMA)
    legacy_db.execute("INSERT INTO snapshots (ts, active, by_tool_json) VALUES (1, 2, '{}')")
    legacy_db.execute("PRAGMA user_version = 3")
    legacy_db.commit()
    legacy_db.close()

    current = connect()
    row = current.execute("SELECT active FROM snapshots WHERE ts = 1").fetchone()
    assert row["active"] == 9
    assert (legacy_home / "history.db").exists()
    current.close()
