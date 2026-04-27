"""Aggregate queries over the local history database."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime


def _midnight_ts() -> int:
    """Unix timestamp for local midnight of today (DST-safe via astimezone)."""
    now = datetime.now().astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp())


@dataclass
class TodayHistoryContext:
    """Single-query snapshot rows for stats and witty-line triggers."""

    midnight: int
    now: int
    rows: list[tuple[int, int]]

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> TodayHistoryContext:
        from overclocked.copy import _SUSTAINED_DURATION_S

        midnight = _midnight_ts()
        now = int(time.time())
        ts_min = min(midnight, now - _SUSTAINED_DURATION_S)
        cur = conn.execute(
            "SELECT ts, active FROM snapshots WHERE ts >= ? AND ts <= ? ORDER BY ts",
            (ts_min, now),
        )
        rows = [(int(r["ts"]), int(r["active"])) for r in cur.fetchall()]
        return cls(midnight, now, rows)

    def today_peak(self) -> tuple[int, int | None]:
        sub = [(t, a) for t, a in self.rows if t >= self.midnight]
        if not sub:
            return (0, None)
        max_a = max(a for _, a in sub)
        first_t = min(t for t, a in sub if a == max_a)
        return (max_a, first_t)

    def today_average(self) -> float:
        sub = [a for t, a in self.rows if t >= self.midnight]
        if not sub:
            return 0.0
        return round(sum(sub) / len(sub), 1)

    def today_sparkline(self) -> list[int]:
        current_hour = (self.now - self.midnight) // 3600
        sub = [(t, a) for t, a in self.rows if self.midnight <= t <= self.now]
        buckets: dict[int, int] = {}
        for t, a in sub:
            hour_bucket = (t - self.midnight) // 3600
            buckets[hour_bucket] = max(buckets.get(hour_bucket, 0), a)
        return [buckets.get(h, 0) for h in range(current_hour + 1)]
