"""Tests for the storage layer."""

from __future__ import annotations

import time

import pytest

from overclocked.detectors import Session
from overclocked.storage import (
    close_session,
    connect,
    open_session,
    prune,
    reconcile,
    write_snapshot,
)


@pytest.fixture
def db(tmp_path):
    return connect(tmp_path / "test.db")


# ── schema / migrations ───────────────────────────────────────────────────────


def test_connect_creates_tables(db):
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "snapshots" in tables
    assert "sessions" in tables


def test_migrations_bring_user_version_to_3(tmp_path):
    db = connect(tmp_path / "fresh.db")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    assert version == 3


def test_migrations_idempotent(tmp_path):
    path = tmp_path / "idempotent.db"
    db1 = connect(path)
    v1 = db1.execute("PRAGMA user_version").fetchone()[0]
    db1.close()
    db2 = connect(path)
    v2 = db2.execute("PRAGMA user_version").fetchone()[0]
    assert v1 == v2 == 3


def test_migration_converts_loaded_schema_to_active(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE snapshots (
            ts           INTEGER PRIMARY KEY,
            loaded       INTEGER NOT NULL,
            hot          INTEGER NOT NULL,
            by_tool_json TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tool        TEXT    NOT NULL,
            project     TEXT,
            started_at  INTEGER NOT NULL,
            ended_at    INTEGER,
            pid         INTEGER,
            session_key TEXT
        )
    """)
    conn.execute(
        "INSERT INTO snapshots (ts, loaded, hot, by_tool_json) VALUES (?,?,?,?)",
        (123, 4, 2, '{"claude": 4}'),
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    conn.close()

    migrated = connect(path)
    columns = [row[1] for row in migrated.execute("PRAGMA table_info(snapshots)").fetchall()]
    assert columns == ["ts", "active", "by_tool_json"]
    row = migrated.execute("SELECT * FROM snapshots WHERE ts = 123").fetchone()
    assert row["active"] == 4
    assert row["by_tool_json"] == '{"claude": 4}'


def test_migration_rollback_on_error(tmp_path):
    """A failing migration leaves user_version unchanged."""
    from overclocked.storage import _MIGRATIONS, _run_migrations

    # Connect first so v1 and v2 run successfully
    db = connect(tmp_path / "rollback.db")
    version_before = db.execute("PRAGMA user_version").fetchone()[0]

    def bad_migration(conn):
        conn.execute("this is not valid SQL")

    _MIGRATIONS.append(bad_migration)
    try:
        with pytest.raises(Exception):
            _run_migrations(db)
        version_after = db.execute("PRAGMA user_version").fetchone()[0]
        assert version_after == version_before
    finally:
        _MIGRATIONS.pop()


def test_wal_pragma_applied(tmp_path):
    db = connect(tmp_path / "wal.db")
    mode = db.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


# ── snapshots ─────────────────────────────────────────────────────────────────


def test_write_snapshot(db):
    write_snapshot(db, active=3, by_tool={"claude": 2, "cursor_editor": 1})
    row = db.execute("SELECT * FROM snapshots").fetchone()
    assert row["active"] == 3
    assert "claude" in row["by_tool_json"]


def test_write_snapshot_ignore_on_same_second(db):
    """Same-second double write is a no-op (INSERT OR IGNORE)."""
    ts = int(time.time())
    db.execute(
        "INSERT INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ts, 5, "{}"),
    )
    db.commit()
    # write_snapshot at same ts should be silently ignored
    import unittest.mock as mock

    with mock.patch("overclocked.storage.time.time", return_value=ts):
        write_snapshot(db, active=99, by_tool={})

    row = db.execute("SELECT active FROM snapshots WHERE ts = ?", (ts,)).fetchone()
    assert row["active"] == 5  # first write preserved


# ── open / close session ──────────────────────────────────────────────────────


def test_open_and_close_session(db):
    sid = open_session(db, "claude", "overclocked", 1234, "claude:/dev/overclocked")
    db.commit()
    assert sid > 0

    row = db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row["ended_at"] is None
    assert row["tool"] == "claude"

    close_session(db, sid)
    db.commit()
    row = db.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row["ended_at"] is not None


def test_open_session_no_auto_commit(db):
    """open_session does not commit; an uncommitted insert is not visible on another conn."""
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        conn1 = connect(path)
        open_session(conn1, "claude", None, 1, "k1")
        # Do not commit — another connection should see no open sessions
        conn2 = connect(path)
        rows = conn2.execute("SELECT * FROM sessions WHERE ended_at IS NULL").fetchall()
        assert len(rows) == 0
        conn1.close()
        conn2.close()
    finally:
        os.unlink(path)


# ── reconcile ─────────────────────────────────────────────────────────────────


def test_reconcile_opens_new_session(db):
    sessions = [Session(tool="claude", pid=101, cwd="/dev/myproject", project="myproject")]
    reconcile(db, sessions)

    rows = db.execute("SELECT * FROM sessions WHERE ended_at IS NULL").fetchall()
    assert len(rows) == 1
    assert rows[0]["tool"] == "claude"
    assert rows[0]["project"] == "myproject"


def test_reconcile_closes_disappeared_session(db):
    sid = open_session(db, "codex", "proj", 202, "codex:/dev/proj")
    db.commit()

    reconcile(db, [])

    row = db.execute("SELECT ended_at FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row["ended_at"] is not None


def test_reconcile_pid_reuse(db):
    reconcile(db, [Session(tool="claude", pid=303, cwd="/dev/proj", project="proj")])
    reconcile(db, [])
    reconcile(db, [Session(tool="claude", pid=303, cwd="/dev/proj", project="proj")])

    all_rows = db.execute("SELECT * FROM sessions").fetchall()
    assert len(all_rows) == 2
    open_rows = [r for r in all_rows if r["ended_at"] is None]
    assert len(open_rows) == 1


def test_reconcile_uses_session_project_directly(db):
    """reconcile reads Session.project rather than re-deriving from cwd."""
    s = Session(tool="claude", pid=99, cwd="/clients/acme", project="redacted")
    reconcile(db, [s])
    row = db.execute("SELECT project FROM sessions WHERE ended_at IS NULL").fetchone()
    assert row["project"] == "redacted"


def test_reconcile_atomic_on_error(db):
    """If reconcile raises before committing, DB state is unchanged."""
    from unittest.mock import patch

    sid = open_session(db, "claude", "proj", 500, "k")
    db.commit()

    original_close = close_session

    def exploding_close(conn, session_id):
        original_close(conn, session_id)
        raise RuntimeError("simulated failure")

    with patch("overclocked.storage.close_session", side_effect=exploding_close):
        try:
            reconcile(db, [])
        except RuntimeError:
            pass

    # The session should still be open since the transaction was rolled back
    row = db.execute("SELECT ended_at FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert row["ended_at"] is None


# ── prune ─────────────────────────────────────────────────────────────────────


def test_prune_deletes_very_old_data(db):
    ancient = int(time.time()) - 400 * 86400
    db.execute(
        "INSERT INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ancient, 1, "{}"),
    )
    db.commit()
    prune(db)
    rows = db.execute("SELECT * FROM snapshots").fetchall()
    assert all(r["ts"] >= int(time.time()) - 366 * 86400 for r in rows)


def test_prune_downsamples_90_day_old_data(db):
    ts_old = ((int(time.time()) - 91 * 86400) // 60) * 60
    for i in range(10):
        db.execute(
            "INSERT INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
            (ts_old + i, i % 4, "{}"),
        )
    db.commit()
    prune(db)
    rows = db.execute("SELECT * FROM snapshots WHERE ts <= ?", (ts_old + 60,)).fetchall()
    assert len(rows) <= 1


def test_prune_summary_uses_insert_or_replace(db):
    """Summary ts colliding with a surviving fine-grained row: summary wins, one row remains."""
    ts_old = ((int(time.time()) - 91 * 86400) // 60) * 60
    # Insert one row that will be downsampled and one at the exact bucket boundary
    db.execute(
        "INSERT INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ts_old, 3, "{}"),
    )
    db.commit()
    prune(db)
    rows = db.execute("SELECT * FROM snapshots WHERE ts = ?", (ts_old,)).fetchall()
    assert len(rows) == 1  # exactly one row at that ts
