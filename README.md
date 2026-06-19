# overclocked

A macOS menu bar widget that counts how many AI coding sessions are active right now,
across Claude Code, Cursor, and Codex. It exists to make "I have twelve agents running"
visible before it turns into AI brain-fry. Click the count for a dropdown of project
names and today's local history.

## What it tracks

- Claude Code CLI sessions and Claude Desktop project sessions.
- Cursor editor and agent workspaces, collapsed to one row per workspace.
- Codex CLI sessions and Codex Desktop rollout files.
- Today's peak, average, and hourly sparkline from local SQLite history.

Dropdown rows are compact by default. Where a tool exposes transcript data, rows can
show model and context hints (opt-in, see Configuration). Cursor rows never show model
or token metrics — the data isn't there.

No network calls, telemetry, or remote storage. Everything stays under `~/.overclocked/`.

## Install

```bash
git clone https://github.com/adamthuvesen/overclocked
cd overclocked
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

This installs the `overclocked` CLI. The package has no runtime dependencies beyond the
standard library; dev extras are `pytest` and `ruff`.

## Demo

Run a no-secret demo. It does not scan local sessions or write history.

```bash
overclocked --demo
```

Representative output:

```text
👾  3
---
Claude Code | color=#6F5543 size=13 sfimage=terminal
  api | color=#E8730A size=12 trim=false
Cursor | color=#6F5543 size=13 sfimage=cursorarrow.rays
  docs | color=#E8730A size=12 trim=false
Codex | color=#6F5543 size=13 sfimage=cube
  app | color=#E8730A size=12 trim=false
---
three. getting jazzy. | font=Georgia-Italic color=#B59F90 size=12
---
No history yet | color=#9D887A
```

Public claim: `overclocked --demo` always shows 3 active demo sessions.

## SwiftBar

Install [SwiftBar](https://github.com/swiftbar/SwiftBar), then symlink the plugin:

```bash
PLUGINS_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
mkdir -p "$PLUGINS_DIR"
ln -sf "$(pwd)/scripts/overclocked.py" "$PLUGINS_DIR/overclocked.py"
chmod +x "$PLUGINS_DIR/overclocked.py"
```

Restart SwiftBar. With no sessions active you'll see `👾  0` in the menu bar. The plugin
keeps a long-lived `overclocked --stream` running and updates every 5 seconds.

## Native menu bar app (for Raycast)

Raycast launches `.app` bundles, not SwiftBar scripts. To get Overclocked as a Raycast
result with its own icon, build the native wrapper:

```bash
scripts/build-menubar-app.sh
```

This installs `~/Applications/Overclocked.app` — a background menu bar app with no window
or Dock item. It runs `overclocked --once` every 5 seconds and logs to
`~/.overclocked/native-menubar.log`.

## Configuration

Runtime files live in `~/.overclocked/` (override with `OVERCLOCKED_HOME`):

- `config.toml` — optional privacy/display config.
- `history.db` — SQLite snapshots and session history.
- `error.log` — best-effort runtime exception log.

Example `config.toml`, showing the defaults:

```toml
[privacy]
# Redact project paths under these roots. Matches path segments, not sibling prefixes.
redact_paths = ["~/clients/"]

[display]
# Show Claude/Codex model and context-token totals.
session_metrics = false
# Show per-session status (working/waiting/done) in the dropdown.
session_status = false
# Show live subagent rows nested under their parent session.
show_subagents = true
```

## CLI

```bash
overclocked                         # one SwiftBar render (guarded error output)
overclocked --once                  # same one-shot render
overclocked --stream --interval 5   # foreground stream loop (interval > 0)
overclocked --dump-state            # raw one-tick detector sample as JSON
overclocked --dump-state-stable     # debounced, menu-stable session list as JSON
overclocked --demo                  # deterministic no-secret sample menu
overclocked --prune                 # prune and downsample old history
```

`--dump-state` skips the menu debounce (best for detector debugging); `--dump-state-stable`
uses the same stable-session path as the menu bar.

`--prune` deletes snapshots older than a year and downsamples those older than 90 days to
one row per minute (median count). To run it daily, add a cron line pointing at your venv:

```cron
0 3 * * * /path/to/.venv/bin/overclocked --prune >> ~/.overclocked/prune.log 2>&1
```

## Development

```bash
uv sync --extra dev
uv run pre-commit install
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q
```

Module map:

- `detectors.py` — process/transcript detection and session enrichment.
- `render.py` — SwiftBar menu rendering.
- `storage.py` — SQLite migrations, snapshots, and pruning.
- `aggregates.py` — today's peak/average/sparkline queries.
- `config.py` — TOML config and path redaction.
- `scripts/overclocked.py` — SwiftBar streamable plugin wrapper.
- `scripts/build-menubar-app.sh` — native Raycast-launchable app builder.
