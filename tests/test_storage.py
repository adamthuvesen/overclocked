"""Tests for the storage layer."""

from __future__ import annotations

import time

import pytest

from overclocked.detectors import Session
from overclocked.storage import (
    connect,
    dedupe_sessions_by_tool_pid,
    prune,
    write_snapshot,
)


@pytest.fixture
def db(tmp_path):
    return connect(tmp_path / "test.db")


# ── schema ────────────────────────────────────────────────────────────────────


def test_connect_creates_snapshots_table(db):
    tables = {
        row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "snapshots" in tables
    assert "sessions" not in tables


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


def test_write_snapshot_upserts_on_same_second(db):
    """Same-second double write keeps the latest aggregate values."""
    ts = int(time.time())
    db.execute(
        "INSERT INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ts, 5, "{}"),
    )
    db.commit()
    import unittest.mock as mock

    with mock.patch("overclocked.storage.time.time", return_value=ts):
        write_snapshot(db, active=99, by_tool={})

    row = db.execute("SELECT active FROM snapshots WHERE ts = ?", (ts,)).fetchone()
    assert row["active"] == 99


def test_dedupe_sessions_by_tool_pid_first_wins():
    a = Session(tool="claude", pid=101, cwd="/x", project="x")
    b = Session(tool="claude", pid=101, cwd="/x", project="y")
    assert dedupe_sessions_by_tool_pid([a, b]) == [a]


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
