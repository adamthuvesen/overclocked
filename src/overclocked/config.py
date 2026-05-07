from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from overclocked.runtime_home import runtime_home


def _warn(config_path: Path, message: str) -> None:
    print(f"overclocked: warning: {config_path}: {message}", file=sys.stderr)


def _table_section(data: dict, name: str, config_path: Path) -> dict:
    section = data.get(name, {})
    if isinstance(section, dict):
        return section
    _warn(config_path, f"{name} must be a TOML table; using defaults")
    return {}


@dataclass
class Config:
    redact_paths: list[str] = field(default_factory=lambda: ["~/clients/"])
    session_status: bool = False
    session_metrics: bool = False
    show_subagents: bool = True

    def is_redacted(self, cwd: str | None) -> bool:
        """Return True if cwd is inside any configured redaction root."""
        if cwd is None:
            return False
        expanded = Path(cwd).expanduser().as_posix()
        for prefix in self._expanded_prefixes():
            if prefix == "/" or expanded == prefix or expanded.startswith(f"{prefix}/"):
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
        _warn(config_path, str(exc))
        return Config()
    privacy = _table_section(data, "privacy", config_path)
    redact_paths = privacy.get("redact_paths", ["~/clients/"])
    if not isinstance(redact_paths, list) or not all(isinstance(p, str) for p in redact_paths):
        _warn(config_path, "redact_paths must be list[str]; using defaults")
        return Config()
    display = _table_section(data, "display", config_path)
    session_status = False
    if "session_status" in display:
        raw_ss = display.get("session_status")
        if isinstance(raw_ss, bool):
            session_status = raw_ss
        else:
            _warn(config_path, "session_status must be bool; using default false")
    session_metrics = False
    if "session_metrics" in display:
        raw_sm = display.get("session_metrics")
        if isinstance(raw_sm, bool):
            session_metrics = raw_sm
        else:
            _warn(config_path, "session_metrics must be bool; using default false")
    show_subagents = True
    if "show_subagents" in display:
        raw_ss2 = display.get("show_subagents")
        if isinstance(raw_ss2, bool):
            show_subagents = raw_ss2
        else:
            _warn(config_path, "show_subagents must be bool; using default true")
    return Config(
        redact_paths=redact_paths,
        session_status=session_status,
        session_metrics=session_metrics,
        show_subagents=show_subagents,
    )
