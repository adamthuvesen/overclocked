# AGENTS.md — Overclocked

Overclocked is a macOS menu bar widget that counts how many AI coding sessions (Claude Code, Cursor, Codex) are active right now. On-device only: no network, no telemetry, pure stdlib.

User-level guidance (tone, principles, git etiquette) lives in `~/.claude/CLAUDE.md` and `~/dotfiles/agents/AGENTS.md` and is *not* duplicated here. This file is for project-specific facts.

## Layout

```
src/overclocked/
├── detectors.py    Process/transcript detection + session enrichment (the big one)
├── render.py       SwiftBar menu rendering
├── storage.py      SQLite migrations, snapshots, pruning
├── aggregates.py   Today's peak/average/hourly sparkline queries
├── config.py       TOML config + path redaction
├── cli.py          Argparse entry point (overclocked CLI)
└── ...             transcript_metrics, transcript_time, sampler_state,
                    runtime_home, identity, copy, _subprocess
native/             Swift menu-bar app (OverclockedMenuBar.swift) + icon generator
scripts/            overclocked.py (SwiftBar wrapper), build-menubar-app.sh
tests/              pytest suite, one file per module
```

Runtime files live under `~/.overclocked/` (override with `OVERCLOCKED_HOME`): `config.toml`, `history.db`, `error.log`.

## Quickstart

```bash
uv sync --extra dev                      # install (dev extras: pytest, ruff, pre-commit)
uv run pytest -q                         # tests
uv run ruff check src tests              # lint
uv run ruff format --check src tests     # format check (line-length 100)
overclocked --demo                       # deterministic no-secret sample menu
```

## Critical Conventions

- **No network, telemetry, or remote storage.** Runtime deps are empty (`pyproject.toml`) — stdlib only is a design constraint, not an accident. Everything stays under `~/.overclocked/`. Don't add a third-party dependency or any outbound call.
- **Cursor rows never show model or token metrics.** Cursor doesn't expose transcript data, so the data doesn't exist — don't fake it. Only Claude/Codex rows can carry model/context hints, and only when opt-in `[display] session_metrics` is set.
- **`overclocked --demo` shows exactly 3 demo sessions and nothing else.** It returns from [`main`](src/overclocked/cli.py) before `load_config()` or `connect()` — it must never scan local sessions or write history. The 3 sessions are hardcoded in `_demo_sessions()`.
- **`--dump-state` vs `--dump-state-stable`.** `--dump-state` is a raw one-tick detector sample (no menu debounce — for detector debugging); `--dump-state-stable` runs the same debounced stable-session path as the menu bar. Pick the right one when debugging.
- **SwiftBar plugin and native app are separate delivery paths.** [`scripts/overclocked.py`](scripts/overclocked.py) drives `--stream` for SwiftBar; [`native/`](native) + [`scripts/build-menubar-app.sh`](scripts/build-menubar-app.sh) build the Raycast-launchable `.app` running `--once`. Keep both working when you touch render or CLI behavior.
- **Never commit secrets, `.env`, or AI-attribution lines.**

## Index

See [README.md](README.md) for the full module map, CLI table, and `config.toml` reference. If a doc disagrees with code, fix the doc in the same change.
