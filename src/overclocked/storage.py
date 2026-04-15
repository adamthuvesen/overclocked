"""SQLite persistence for session snapshots and session events."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from overclocked.detectors import Session
from overclocked.identity import session_key
from overclocked.runtime_home import runtime_home

# ── migrations ────────────────────────────────────────────────────────────────


def _migration_v1(conn: sqlite3.Connection) -> None:
    """Create base tables (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            ts           INTEGER PRIMARY KEY,
            active       INTEGER NOT NULL,
            by_tool_json TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tool        TEXT    NOT NULL,
            project     TEXT,
            started_at  INTEGER NOT NULL,
            ended_at    INTEGER,
            pid         INTEGER,
            session_key TEXT
        )
    """)


def _migration_v2(conn: sqlite3.Connection) -> None:
    """Add performance indexes."""
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_open ON sessions(ended_at) WHERE ended_at IS NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts)")


def _migration_v3(conn: sqlite3.Connection) -> None:
    """Rename snapshot metric from loaded/hot to a single active count."""
    columns = [row[1] for row in conn.execute("PRAGMA table_info(snapshots)")]
    if not columns:
        return
    if columns == ["ts", "active", "by_tool_json"]:
        return

    conn.execute("""
        CREATE TABLE snapshots_v3 (
            ts           INTEGER PRIMARY KEY,
            active       INTEGER NOT NULL,
            by_tool_json TEXT    NOT NULL
        )
    """)

    if "loaded" in columns:
        conn.execute("""
            INSERT INTO snapshots_v3 (ts, active, by_tool_json)
            SELECT ts, loaded, by_tool_json FROM snapshots
        """)
    else:
        conn.execute("""
            INSERT INTO snapshots_v3 (ts, active, by_tool_json)
            SELECT ts, active, by_tool_json FROM snapshots
        """)

    conn.execute("DROP TABLE snapshots")
    conn.execute("ALTER TABLE snapshots_v3 RENAME TO snapshots")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts)")


_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migration_v1,
    _migration_v2,
    _migration_v3,
]


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply pending migrations in order using PRAGMA user_version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, migration in enumerate(_MIGRATIONS, start=1):
        if current >= version:
            continue
        with conn:
            migration(conn)
            conn.execute(f"PRAGMA user_version = {version}")
        current = version


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open (or create) the history database, apply pragmas, and run migrations."""
    if db_path is None:
        home = runtime_home()
        home.mkdir(parents=True, exist_ok=True)
        db_path = home / "history.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    _run_migrations(conn)
    return conn


def write_snapshot(
    conn: sqlite3.Connection,
    active: int,
    by_tool: dict[str, int],
) -> None:
    import json

    ts = int(time.time())
    # INSERT OR IGNORE: same-second double-write is a no-op (first write wins)
    conn.execute(
        "INSERT OR IGNORE INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ts, active, json.dumps(by_tool)),
    )
    conn.commit()


def open_session(
    conn: sqlite3.Connection,
    tool: str,
    project: str | None,
    pid: int,
    key: str | None = None,
) -> int:
    """Insert a new session row and return its id. Caller is responsible for commit."""
    ts = int(time.time())
    cur = conn.execute(
        "INSERT INTO sessions (tool, project, started_at, pid, session_key) VALUES (?,?,?,?,?)",
        (tool, project, ts, pid, key),
    )
    return cur.lastrowid  # type: ignore[return-value]


def close_session(conn: sqlite3.Connection, session_id: int) -> None:
    """Set ended_at for the given session. Caller is responsible for commit."""
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE id = ?",
        (int(time.time()), session_id),
    )


def reconcile(
    conn: sqlite3.Connection,
    current_sessions: list[Session],
) -> None:
    """Open new sessions and close disappeared ones in a single atomic transaction."""
    with conn:
        # Build current map using already-resolved Session.cwd and Session.project
        current_map: dict[tuple[str, int], tuple[str | None, str | None]] = {}
        for s in current_sessions:
            key = session_key(s.tool, s.cwd, s.pid)
            current_map[(s.tool, s.pid)] = (key, s.project)

        # Fetch all open sessions from the DB (ended_at IS NULL)
        rows = conn.execute(
            "SELECT id, tool, pid, session_key FROM sessions WHERE ended_at IS NULL"
        ).fetchall()
        open_pids: dict[tuple[str, int], int] = {
            (row["tool"], row["pid"]): row["id"] for row in rows
        }

        # Close sessions no longer present
        current_pids = set(current_map.keys())
        for (tool, pid), db_id in list(open_pids.items()):
            if (tool, pid) not in current_pids:
                close_session(conn, db_id)

        # Open new sessions not yet in the DB
        for s in current_sessions:
            if (s.tool, s.pid) not in open_pids:
                key = session_key(s.tool, s.cwd, s.pid)
                open_session(conn, s.tool, s.project, s.pid, key)


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
        import statistics
        from collections import defaultdict

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
