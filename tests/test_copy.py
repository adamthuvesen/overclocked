"""Tests for stateful witty copy generation."""

from __future__ import annotations

import time

import pytest

from overclocked.aggregates import _midnight_ts
from overclocked.copy import (
    _STABLE_ESCALATION_S,
    _SUSTAINED_COUNT,
    _SUSTAINED_DURATION_S,
    choose_line,
)
from overclocked.storage import connect


@pytest.fixture
def db(tmp_path):
    return connect(tmp_path / "test.db")


def _insert(db, offset_seconds: int, active: int) -> None:
    ts = int(time.time()) - offset_seconds
    db.execute(
        "INSERT OR REPLACE INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
        (ts, active, "{}"),
    )
    db.commit()


# ── no history ────────────────────────────────────────────────────────────────


def test_no_conn_uses_fallback():
    assert choose_line(0) == "nothing running. suspicious."
    assert choose_line(3) == "three. getting jazzy."
    assert choose_line(99) == "the AIs are overclocked now."


def test_empty_db_uses_fallback(db):
    result = choose_line(2, conn=db)
    assert result == "two is a pair. reasonable."


# ── stable trigger ────────────────────────────────────────────────────────────


def test_stable_for_30min(db):
    for i in range(10):
        _insert(db, 35 * 60 - i * 200, 3)
    result = choose_line(3, conn=db)
    assert "still 3" in result or "committed" in result


def test_stable_at_zero(db):
    for i in range(5):
        _insert(db, 35 * 60 - i * 400, 0)
    result = choose_line(0, conn=db)
    assert result == "nothing running. are you okay?"


def test_stable_escalation_fires_after_2h(db):
    """After 2 h stable, the escalation tier fires instead of 'committed'."""
    for i in range(20):
        _insert(db, _STABLE_ESCALATION_S + 60 - i * 400, 3)
    result = choose_line(3, conn=db)
    assert "unhinged" in result


# ── sharp drop trigger ────────────────────────────────────────────────────────


def test_sharp_drop(db):
    _insert(db, 3 * 60, 4)
    result = choose_line(1, conn=db)
    assert "back to one" in result


def test_no_trigger_for_small_drop(db):
    _insert(db, 3 * 60, 2)
    result = choose_line(1, conn=db)
    assert "back to one" not in result


def test_sharp_drop_uses_max_over_window(db):
    """Sharp drop uses MAX(active) over the 5-min window, not the oldest row."""
    now = int(time.time())
    # Series: values dipped and recovered inside the window; current=1
    for offset, val in [(280, 4), (240, 2), (200, 4), (160, 4), (30, 4)]:
        db.execute(
            "INSERT OR REPLACE INTO snapshots (ts, active, by_tool_json) VALUES (?,?,?)",
            (now - offset, val, "{}"),
        )
    db.commit()
    result = choose_line(1, conn=db)
    # MAX over window is 4; drop = 4-1 = 3 >= _DROP_AMOUNT=2, should trigger
    assert "back to one" in result


# ── peak match trigger ────────────────────────────────────────────────────────


def test_peak_match(db):
    midnight = _midnight_ts()
    db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (midnight + 10, 2, "{}"))
    db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (midnight + 20, 4, "{}"))
    db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (midnight + 30, 2, "{}"))
    db.commit()
    result = choose_line(4, conn=db)
    assert "back to 4" in result or "before" in result


# ── sustained high load trigger ───────────────────────────────────────────────


def test_sustained_high_load_fires(db):
    """5+ sessions for ≥1 hour triggers the sustained-load copy."""
    for i in range(20):
        _insert(db, _SUSTAINED_DURATION_S + 60 - i * 200, _SUSTAINED_COUNT)
    result = choose_line(_SUSTAINED_COUNT, conn=db)
    assert any(phrase in result for phrase in ["hour", "overclock", "swarm", "coordinator"])


def test_sustained_high_load_clears(db):
    """Condition clears when count drops below threshold."""
    # History shows 5 sessions sustained, but current count is 2
    for i in range(10):
        _insert(db, _SUSTAINED_DURATION_S + 60 - i * 200, _SUSTAINED_COUNT)
    result = choose_line(2, conn=db)
    # Should not fire sustained trigger for current=2
    assert not any(phrase in result for phrase in ["coordinator", "swarm"])


# ── fallback covers high counts ───────────────────────────────────────────────


def test_fallback_clamped_for_high_counts():
    assert choose_line(100) == "the AIs are overclocked now."
