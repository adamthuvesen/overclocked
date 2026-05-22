from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from overclocked.runtime_home import runtime_home

_DEFAULT_REDACT_PATHS = ["~/clients/"]


def _warn(config_path: Path, message: str) -> None:
    print(f"overclocked: warning: {config_path}: {message}", file=sys.stderr)


def _table_section(data: dict, name: str, config_path: Path) -> dict:
    section = data.get(name, {})
    if isinstance(section, dict):
        return section
    _warn(config_path, f"{name} must be a TOML table; using defaults")
    return {}


def _bool_setting(data: dict, name: str, default: bool, config_path: Path) -> bool:
    if name not in data:
        return default
    value = data.get(name)
    if isinstance(value, bool):
        return value
    _warn(config_path, f"{name} must be bool; using default {str(default).lower()}")
    return default


@dataclass
class Config:
    redact_paths: list[str] = field(default_factory=lambda: list(_DEFAULT_REDACT_PATHS))
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
            result.append(expanded or "/")
        return result


def load_config() -> Config:
    config_path = runtime_home() / "config.toml"
    if not config_path.exists():
        return Config()
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        _warn(config_path, str(exc))
        return Config()
    except tomllib.TOMLDecodeError as exc:
        _warn(config_path, str(exc))
        return Config()
    privacy = _table_section(data, "privacy", config_path)
    redact_paths = privacy.get("redact_paths", _DEFAULT_REDACT_PATHS)
    if not isinstance(redact_paths, list) or not all(isinstance(p, str) for p in redact_paths):
        _warn(config_path, "redact_paths must be list[str]; using defaults")
        redact_paths = _DEFAULT_REDACT_PATHS
    display = _table_section(data, "display", config_path)
    return Config(
        redact_paths=list(redact_paths),
        session_status=_bool_setting(display, "session_status", False, config_path),
        session_metrics=_bool_setting(display, "session_metrics", False, config_path),
        show_subagents=_bool_setting(display, "show_subagents", True, config_path),
    )
