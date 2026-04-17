"""Stateful witty one-liner generation."""

from __future__ import annotations

import sqlite3

from overclocked.aggregates import TodayHistoryContext

# Count-indexed fallback lines. Index = active session count, last entry used for any higher.
_FALLBACK: list[str] = [
    "nothing running. suspicious.",
    "one copilot. focused.",
    "two is a pair. reasonable.",
    "three. getting jazzy.",
    "four brains. one you.",
    "five copilots. which one's driving?",
    "the AIs are overclocked now.",
]

# Named constants for copy-trigger thresholds
_STABLE_THRESHOLD = 30 * 60  # 30 minutes stable before "committed" fires
_STABLE_ESCALATION_S = 7200  # 2 hours: escalation tier to avoid "still N?" looping

_DROP_WINDOW = 5 * 60  # window for sharp-drop detection
_DROP_AMOUNT = 2

_PEAK_MATCH_FLOOR = 3  # minimum peak for the "back to N" trigger

_SUSTAINED_COUNT = 5  # session count threshold for sustained-high-load trigger
_SUSTAINED_DURATION_S = 3600  # 1 hour sustained to fire the trigger

# Sustained-high-load copy pool (rotates so consecutive ticks vary)
_SUSTAINED_LINES: list[str] = [
    "five for an hour. you're basically a coordinator now.",
    "sustained overclocking. are you even writing code anymore?",
    "an hour at five. the swarm has achieved sentience.",
]


def _fallback(count: int) -> str:
    idx = min(count, len(_FALLBACK) - 1)
    return _FALLBACK[idx]


def choose_line(
    current: int,
    conn: sqlite3.Connection | None = None,
    *,
    ctx: TodayHistoryContext | None = None,
) -> str:
    """Return a one-liner based on current count and recent history.

    Falls back to a count-indexed default if no history is available or
    no stateful trigger fires.
    """
    if conn is None:
        return _fallback(current)

    if ctx is None:
        ctx = TodayHistoryContext.load(conn)

    now = ctx.now
    midnight = ctx.midnight
    rows = ctx.rows

    # ── trigger: sustained high load for ≥1 hour ─────────────────────────────
    if current >= _SUSTAINED_COUNT:
        sustained_since = now - _SUSTAINED_DURATION_S
        in_sustained = [(t, a) for t, a in rows if t >= sustained_since]
        if in_sustained and in_sustained[0][1] >= _SUSTAINED_COUNT:
            idx = (now // 30) % len(_SUSTAINED_LINES)
            return _SUSTAINED_LINES[idx]

    # ── trigger: count stable for ≥30 min ─────────────────────────────────────
    stable_since = now - _STABLE_THRESHOLD
    stable_vals = [a for t, a in rows if t >= stable_since]
    if stable_vals:
        if all(v == current for v in stable_vals) and len(stable_vals) > 1:
            if current == 0:
                return "nothing running. are you okay?"
            if current == 1:
                return "just the one. staying in the zone."
            escalation_since = now - _STABLE_ESCALATION_S
            esc_vals = [a for t, a in rows if t >= escalation_since]
            if esc_vals and all(r == current for r in esc_vals):
                return f"still {current}. unhinged bit."
            return f"still {current}? committed."

    # ── trigger: sharp drop in the last 5 minutes ─────────────────────────────
    drop_since = now - _DROP_WINDOW
    drop_vals = [a for t, a in rows if t >= drop_since]
    if drop_vals:
        prev = max(drop_vals)
        if prev - current >= _DROP_AMOUNT and current <= 1:
            return "back to one. they're fine without you."

    # ── trigger: today's peak matched again ───────────────────────────────────
    since_mid = [(t, a) for t, a in rows if t >= midnight]
    if since_mid:
        peak = max(a for _, a in since_mid)
        if current == peak and peak >= _PEAK_MATCH_FLOOR:
            lower_n = sum(1 for t, a in since_mid if a < peak)
            if lower_n > 0:
                return f"back to {peak}. you've been here before."

    return _fallback(current)
