"""Tests for sampler-state persistence."""

from __future__ import annotations

import threading

from overclocked.sampler_state import load_raw_session_keys, save_raw_session_keys


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr("overclocked.sampler_state.runtime_home", lambda: tmp_path)
    keys = frozenset({("claude", 42), ("codex", 7)})
    save_raw_session_keys(keys)
    assert load_raw_session_keys() == keys


def test_concurrent_save_raw_session_keys(tmp_path, monkeypatch):
    """Parallel tick + Refresh must not clobber a shared *.tmp path."""
    monkeypatch.setattr("overclocked.sampler_state.runtime_home", lambda: tmp_path)

    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            save_raw_session_keys(frozenset({("claude", i)}))
        except BaseException as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(32)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    loaded = load_raw_session_keys()
    assert loaded is not None
    assert len(loaded) == 1
    assert next(iter(loaded))[0] == "claude"
