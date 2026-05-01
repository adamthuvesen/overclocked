"""SQLite persistence for session snapshots."""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import defaultdict
from pathlib import Path

from overclocked.detectors import Session
from overclocked.runtime_home import runtime_home


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open (or create) the history database, apply pragmas, and ensure schema."""
    if db_path is None:
        home = runtime_home()
        home.mkdir(parents=True, exist_ok=True)
        db_path = home / "history.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            ts           INTEGER PRIMARY KEY,
            active       INTEGER NOT NULL,
            by_tool_json TEXT    NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts)")
    return conn


def dedupe_sessions_by_tool_pid(sessions: list[Session]) -> list[Session]:
    """Collapse duplicate (tool, pid) rows; first occurrence wins."""
    seen: set[tuple[str, int]] = set()
    out: list[Session] = []
    for s in sessions:
        k = (s.tool, s.pid)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def write_snapshot(
    conn: sqlite3.Connection,
    active: int,
    by_tool: dict[str, int],
) -> None:
    ts = int(time.time())
    # Same-second double-write: keep latest aggregate values
    conn.execute(
        """
        INSERT INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)
        ON CONFLICT(ts) DO UPDATE SET
          active = excluded.active,
          by_tool_json = excluded.by_tool_json
        """,
        (ts, active, json.dumps(by_tool)),
    )
    conn.commit()


def prune(conn: sqlite3.Connection) -> None:
    """Downsample old snapshots and delete very old data."""
    now = int(time.time())
    ninety_days = now - 90 * 86400
    one_year = now - 365 * 86400

    conn.execute("DELETE FROM snapshots WHERE ts < ?", (one_year,))

    rows = conn.execute(
        "SELECT ts, active FROM snapshots WHERE ts < ? ORDER BY ts",
        (ninety_days,),
    ).fetchall()

    if rows:
        buckets: dict[int, list[int]] = defaultdict(list)
        for row in rows:
            bucket = (row["ts"] // 60) * 60
            buckets[bucket].append(row["active"])

        conn.execute("DELETE FROM snapshots WHERE ts < ?", (ninety_days,))

        # INSERT OR REPLACE so a bucket ts colliding with a surviving fine-grained
        # row produces exactly one row with the summary value.
        for bucket_ts, values in buckets.items():
            median = int(statistics.median(values))
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
                (bucket_ts, median, "{}"),
            )

    conn.commit()
