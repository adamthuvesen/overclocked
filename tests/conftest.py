from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def isolate_overclocked_home(tmp_path, monkeypatch):
    """Redirect all ~/.overclocked access to a temporary directory."""
    overclocked_home = tmp_path / ".overclocked"
    overclocked_home.mkdir()
    monkeypatch.delenv("QUORUM_HOME", raising=False)
    monkeypatch.setenv("OVERCLOCKED_HOME", str(overclocked_home))
    return overclocked_home


@pytest.fixture(autouse=True)
def reset_detector_module_caches():
    """Detector tests call list_* without Sampler.tick(); clear tick-scoped module state."""
    import overclocked.detectors as detectors

    detectors._codex_tick_data = None
    detectors._ps_table = None
    detectors._cwd_cache.clear()
    detectors._mtime_cache.clear()
    yield
    detectors._codex_tick_data = None
    detectors._ps_table = None
    detectors._cwd_cache.clear()
    detectors._mtime_cache.clear()
