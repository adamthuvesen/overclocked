"""Tests for aggregate queries."""

from __future__ import annotations

import time

import pytest

from overclocked.aggregates import TodayHistoryContext, _midnight_ts
from overclocked.storage import connect


@pytest.fixture
def db(tmp_path):
    return connect(tmp_path / "test.db")


def _insert(db, ts: int, active: int) -> None:
    db.execute(
        "INSERT OR REPLACE INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ts, active, "{}"),
    )
    db.commit()


def test_peak_empty_db(db):
    peak, ts = TodayHistoryContext.load(db).today_peak()
    assert peak == 0
    assert ts is None


def test_peak_returns_max(db):
    midnight = _midnight_ts()
    _insert(db, midnight + 100, 2)
    _insert(db, midnight + 200, 5)
    _insert(db, midnight + 300, 3)
    peak, ts = TodayHistoryContext.load(db).today_peak()
    assert peak == 5
    assert ts == midnight + 200


def test_peak_ignores_yesterday(db):
    midnight = _midnight_ts()
    _insert(db, midnight - 3600, 99)
    _insert(db, midnight + 100, 2)
    peak, _ = TodayHistoryContext.load(db).today_peak()
    assert peak == 2


def test_average_empty_db(db):
    assert TodayHistoryContext.load(db).today_average() == 0.0


def test_average_calculation(db):
    midnight = _midnight_ts()
    for i, val in enumerate([1, 2, 3, 4]):
        _insert(db, midnight + i * 100, val)
    avg = TodayHistoryContext.load(db).today_average()
    assert avg == 2.5


def test_average_ignores_yesterday(db):
    midnight = _midnight_ts()
    _insert(db, midnight - 3600, 100)
    _insert(db, midnight + 100, 2)
    assert TodayHistoryContext.load(db).today_average() == 2.0


def test_sparkline_empty_day_has_correct_length(db):
    """Empty day: length = (now - midnight) // 3600 + 1, all zeros."""
    midnight = _midnight_ts()
    now = int(time.time())
    expected_len = (now - midnight) // 3600 + 1
    result = TodayHistoryContext.load(db).today_sparkline()
    assert len(result) == expected_len
    assert all(v == 0 for v in result)


def test_sparkline_single_hour(db):
    midnight = _midnight_ts()
    _insert(db, midnight + 300, 3)
    _insert(db, midnight + 600, 5)
    result = TodayHistoryContext.load(db).today_sparkline()
    assert result[0] == 5


def test_sparkline_multiple_hours(db):
    midnight = _midnight_ts()
    _insert(db, midnight + 100, 2)  # hour 0
    _insert(db, midnight + 3700, 4)  # hour 1
    _insert(db, midnight + 7300, 1)  # hour 2
    result = TodayHistoryContext.load(db).today_sparkline()
    # Length includes all elapsed hours through current hour
    assert len(result) >= 3
    assert result[0] == 2
    assert result[1] == 4
    assert result[2] == 1


def test_sparkline_extends_to_current_hour(db):
    """Sparkline must not be truncated at the last row's hour."""
    midnight = _midnight_ts()
    now = int(time.time())
    expected_len = (now - midnight) // 3600 + 1
    # Insert only at hour 0
    _insert(db, midnight + 100, 3)
    result = TodayHistoryContext.load(db).today_sparkline()
    assert len(result) == expected_len
    assert result[0] == 3
    # Hours after 0 with no data are zero
    assert all(v == 0 for v in result[1:])


def test_today_history_context_peak_matches_direct_sql(db):
    """Golden check: context peak logic matches the historical SQL ordering."""
    midnight = _midnight_ts()
    _insert(db, midnight + 100, 2)
    _insert(db, midnight + 200, 7)
    _insert(db, midnight + 300, 7)
    row = db.execute(
        "SELECT active, ts FROM snapshots WHERE ts >= ? ORDER BY active DESC, ts ASC LIMIT 1",
        (midnight,),
    ).fetchone()
    ctx = TodayHistoryContext.load(db)
    assert ctx.today_peak() == (row["active"], row["ts"])
