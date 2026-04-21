from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from overclocked.runtime_home import runtime_home


@dataclass
class Config:
    redact_paths: list[str] = field(default_factory=lambda: ["~/clients/"])
    session_metrics: bool = True

    def is_redacted(self, cwd: str | None) -> bool:
        """Return True if cwd starts with any redact_paths prefix."""
        if cwd is None:
            return False
        expanded = Path(cwd).expanduser().as_posix()
        for prefix in self._expanded_prefixes():
            if expanded.startswith(prefix):
                return True
        return False

    def _expanded_prefixes(self) -> list[str]:
        result = []
        for p in self.redact_paths:
            expanded = Path(p).expanduser().as_posix().rstrip("/")
            result.append(expanded)
        return result


def load_config() -> Config:
    config_path = runtime_home() / "config.toml"
    if not config_path.exists():
        return Config()
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        print(f"overclocked: warning: {config_path}: {exc}", file=sys.stderr)
        return Config()
    privacy = data.get("privacy", {})
    redact_paths = privacy.get("redact_paths", ["~/clients/"])
    if not isinstance(redact_paths, list) or not all(isinstance(p, str) for p in redact_paths):
        print(
            f"overclocked: warning: {config_path}: redact_paths must be list[str]; using defaults",
            file=sys.stderr,
        )
        return Config()
    display = data.get("display", {})
    session_metrics = True
    if "session_metrics" in display:
        raw_sm = display.get("session_metrics")
        if isinstance(raw_sm, bool):
            session_metrics = raw_sm
        else:
            print(
                f"overclocked: warning: {config_path}: session_metrics must be bool; using default true",
                file=sys.stderr,
            )
    return Config(redact_paths=redact_paths, session_metrics=session_metrics)
