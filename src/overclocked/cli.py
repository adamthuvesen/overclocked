"""CLI entry point for overclocked."""

from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import select
import signal
import sqlite3
import sys
import time
import traceback
from datetime import UTC, datetime

from overclocked.config import Config, load_config
from overclocked.detectors import Session, raw_session_keys, stable_sessions_from_keys, tick
from overclocked.render import RenderState, dropdown
from overclocked.runtime_home import runtime_home
from overclocked.sampler_state import load_raw_session_keys, save_raw_session_keys
from overclocked.storage import (
    connect,
    dedupe_sessions_by_tool_pid,
    prune,
    write_snapshot,
)


def _build_state_dict(sessions):
    """Build a JSON-serialisable state dict for --dump-state."""
    rows = []
    for s in sessions:
        row = {
            "tool": s.tool,
            "pid": s.pid,
            "cwd": s.cwd,
            "project": s.project,
            "status": s.status,
        }
        if s.synthetic:
            row["synthetic"] = True
        if s.model is not None:
            row["model"] = s.model
        if s.input_tokens is not None:
            row["input_tokens"] = s.input_tokens
        if s.output_tokens is not None:
            row["output_tokens"] = s.output_tokens
        if s.cache_read is not None:
            row["cache_read"] = s.cache_read
        if s.cache_create is not None:
            row["cache_create"] = s.cache_create
        rows.append(row)
    return {"sessions": rows, "active": len(sessions)}


def _render_once(
    config: Config,
    conn: sqlite3.Connection,
    prev_raw_keys: frozenset[tuple[str, int]],
) -> tuple[str, frozenset[tuple[str, int]], list[Session]]:
    """Run one sample + render cycle.

    Returns (swiftbar_output, current_raw_keys, stable_sessions). Stable
    sessions are the intersection of the current sample with ``prev_raw_keys``;
    on a true cold start (``prev_raw_keys`` empty) the first frame is empty by
    design — the caller is expected to feed the returned keys back in for the
    next tick.
    """
    curr = tick(config)
    k_curr = raw_session_keys(curr)
    sessions = dedupe_sessions_by_tool_pid(stable_sessions_from_keys(curr, prev_raw_keys))

    by_tool: dict[str, int] = {}
    for s in sessions:
        by_tool[s.tool] = by_tool.get(s.tool, 0) + 1
    active = len(sessions)

    write_snapshot(conn, active=active, by_tool=by_tool)

    state = RenderState(sessions=sessions, conn=conn, config=config)
    return dropdown(state), k_curr, sessions


def _log_exception(exc: BaseException) -> None:
    """Append a traceback entry to ~/.overclocked/error.log; never raise."""
    try:
        error_log = runtime_home() / "error.log"
        error_log.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        tb = traceback.format_exc()
        with error_log.open("a") as f:
            f.write(f"[{ts}] {type(exc).__name__}: {first_line}\n{tb}\n")
    except OSError:
        pass


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite number greater than 0")
    return parsed


def _run_once(config: Config) -> None:
    """Single SwiftBar render — used by --once.

    On cold start (no persisted keys) does a bare priming tick first so the
    rendered tick has a real debounce baseline; otherwise debounces against
    the persisted keys from the previous run. Only the rendered tick writes
    a snapshot.
    """
    persisted = load_raw_session_keys()
    k_prev = raw_session_keys(tick(config)) if persisted is None else persisted
    with contextlib.closing(connect()) as conn:
        output, k_curr, _ = _render_once(config, conn, k_prev)
    save_raw_session_keys(k_curr)
    print(output)


def _dump_state_stable_sessions(config) -> list[Session]:
    """One-shot debounced sample for ``--dump-state-stable``."""
    persisted = load_raw_session_keys()
    if persisted is None:
        k_prev = raw_session_keys(tick(config))
    else:
        k_prev = persisted
    curr = tick(config)
    save_raw_session_keys(raw_session_keys(curr))
    return stable_sessions_from_keys(curr, k_prev)


def _run_stream(config: Config, interval: float) -> None:
    """Long-lived SwiftBar streamable loop.

    Holds an open SQLite connection and the previous raw-key set in memory so
    subprocess fan-out and disk debounce vanish from the hot path. Emits a
    full SwiftBar menu followed by the streamable separator ``~~~`` between
    updates. Inner exceptions are logged and surfaced as an error frame; the
    loop continues so a single bad tick does not kill the menu.
    """
    stop = {"flag": False}

    def _request_stop(signum, frame):  # noqa: ARG001
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    signal.signal(signal.SIGHUP, _request_stop)

    # If SwiftBar stops reading, flush() can block forever on a full pipe —
    # we've seen 20+ zombies pile up this way. Guard each write with a
    # select() readiness check so a stalled reader triggers a clean exit
    # instead of an indefinite hang. In tests, stdout is often a StringIO
    # with no real fd — skip the readiness check there.
    write_timeout = max(interval * 2, 5.0)
    try:
        stdout_fd: int | None = sys.stdout.fileno()
    except (OSError, ValueError, AttributeError):
        stdout_fd = None

    def _stdout_ready() -> bool:
        if stdout_fd is None:
            return True
        try:
            _, w, _ = select.select([], [stdout_fd], [], write_timeout)
        except OSError:
            return False
        return bool(w)

    parent_pid = os.getppid()
    persisted = load_raw_session_keys()
    # Cold start: prime the debounce baseline with one bare tick so the first
    # rendered frame already has a real prev set (matches --once behavior).
    prev: frozenset[tuple[str, int]] = (
        persisted if persisted is not None else raw_session_keys(tick(config))
    )

    with contextlib.closing(connect()) as conn:
        while not stop["flag"]:
            # Reparenting to launchd (ppid=1) means SwiftBar is gone; exit.
            if os.getppid() != parent_pid:
                break
            if not _stdout_ready():
                break
            try:
                output, prev, _ = _render_once(config, conn, prev)
                sys.stdout.write(output)
                sys.stdout.write("\n~~~\n")
                sys.stdout.flush()
            except BrokenPipeError:
                break
            except Exception as exc:
                _log_exception(exc)
                first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                try:
                    sys.stdout.write(f"👾 !\n---\n{first_line}\n~~~\n")
                    sys.stdout.flush()
                except BrokenPipeError:
                    break
                except OSError:
                    pass

            # Sleep in short slices so SIGTERM/SIGINT close the loop promptly.
            slept = 0.0
            slice_s = 0.25
            while not stop["flag"] and slept < interval:
                time.sleep(min(slice_s, interval - slept))
                slept += slice_s

        # Best-effort: persist the last seen keys so a follow-up `--once` call
        # has a warm baseline instead of cold-starting another double tick.
        with contextlib.suppress(OSError):
            save_raw_session_keys(prev)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="overclocked",
        description="macOS menu bar AI copilot session counter",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one tick and print SwiftBar output to stdout",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Run as a SwiftBar streamable plugin (long-lived loop)",
    )
    parser.add_argument(
        "--interval",
        type=_positive_float,
        default=5.0,
        help="Seconds between ticks in --stream mode (default: 5)",
    )
    parser.add_argument(
        "--dump-state",
        action="store_true",
        help=(
            "Print raw detector sample (one tick, no debounce) as JSON and exit; "
            "see --dump-state-stable for menu-stable keys"
        ),
    )
    parser.add_argument(
        "--dump-state-stable",
        action="store_true",
        help=("Print debounced session list (same stability as the menubar) as JSON and exit"),
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Prune old history from the database and exit",
    )
    args = parser.parse_args(argv)

    # --prune and --dump-state bypass the exception guard so tracebacks surface.
    if args.prune:
        with contextlib.closing(connect()) as conn:
            prune(conn)
        print("Pruned history database.")
        return

    config = load_config()

    if args.dump_state_stable:
        sessions = dedupe_sessions_by_tool_pid(_dump_state_stable_sessions(config))
        print(json.dumps(_build_state_dict(sessions), indent=2))
        return

    if args.dump_state:
        print(json.dumps(_build_state_dict(tick(config)), indent=2))
        return

    if args.stream:
        # Stream mode owns its own resilience loop — no outer exception guard so
        # a fatal error (e.g. unable to open the DB) still surfaces a traceback.
        _run_stream(config, args.interval)
        return

    # SwiftBar render path — wrap in exception guard so any failure produces
    # a visible menubar glyph instead of a raw traceback.
    try:
        _run_once(config)
    except Exception as exc:
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        print(f"👾 !\n---\n{first_line}")
        traceback.print_exc(file=sys.stderr)
        _log_exception(exc)


if __name__ == "__main__":
    main()
