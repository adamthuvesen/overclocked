# overclocked

A macOS SwiftBar widget that counts active AI coding sessions across Claude Code,
Cursor, and Codex, with a small dropdown for project names and today's local history.

The menu bar count includes every detected session. Dropdown rows are compact by
default, and can optionally show model/context hints where the underlying tool
exposes transcript data.

## What it tracks

- Claude Code CLI sessions and Claude Desktop-backed project sessions.
- Cursor editor and agent workspaces, collapsed to one row per workspace.
- Codex CLI sessions and Codex Desktop rollout files.
- Today's peak, average, and hourly sparkline from the local SQLite history.

Cursor rows intentionally do not show model or token metrics.

## Install

```bash
git clone <repo>
cd overclocked
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

This installs the `overclocked` CLI from `overclocked.cli:main`. The package has no
runtime Python dependencies beyond the standard library; dev extras install `pytest`
and `ruff`.

## SwiftBar

Install [SwiftBar](https://github.com/swiftbar/SwiftBar), then symlink the streamable
plugin:

```bash
PLUGINS_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
mkdir -p "$PLUGINS_DIR"
ln -sf "$(pwd)/scripts/overclocked.py" "$PLUGINS_DIR/overclocked.py"
chmod +x "$PLUGINS_DIR/overclocked.py"
```

Restart SwiftBar. You should see `👾  0` in the menu bar when no sessions are active.

The plugin runs `overclocked --stream`, keeps a long-lived process open, and emits
SwiftBar stream separators between updates. The default stream interval is 5 seconds.

## Raycast / Native Menu Bar App

Raycast launches macOS `.app` bundles, not SwiftBar plugin scripts. To make
Overclocked show up as a normal Raycast application result with its own icon, build
and install the native menu bar wrapper:

```bash
scripts/build-menubar-app.sh
```

This installs `~/Applications/Overclocked.app`. Opening it from Raycast starts a
background menu bar app; it does not open a window or Dock item. The wrapper runs
`overclocked --once` every 5 seconds and writes launch/debug details to
`~/.overclocked/native-menubar.log`.

## Configuration

Runtime files live in `~/.overclocked/` by default:

- `config.toml`: optional privacy/display configuration.
- `history.db`: local SQLite snapshots and session history.
- `error.log`: best-effort stream/runtime exception log.

Set `OVERCLOCKED_HOME` to use a different runtime directory.

Example `~/.overclocked/config.toml`:

```toml
[privacy]
# Default: ["~/clients/"]; matches path segments, not sibling prefixes.
redact_paths = ["~/clients/", "~/personal/"]

[display]
# Show Claude/Codex model and context-token totals. Default: false.
session_metrics = false
# Show per-session activity status (working/waiting/done) in the dropdown. Default: false.
session_status = false
# Show live subagent rows nested under their parent session. Default: true.
show_subagents = true
```

No network calls, telemetry, or remote storage. Everything overclocked writes stays
under the runtime home.

## CLI

```bash
overclocked                         # one SwiftBar render, with guarded error output
overclocked --once                  # same one-shot render path
overclocked --stream --interval 5   # foreground SwiftBar stream loop; interval must be finite and > 0
overclocked --dump-state            # raw one-tick detector sample as JSON
overclocked --dump-state-stable     # debounced/menu-stable session list as JSON
overclocked --prune                 # prune and downsample old history
```

`--dump-state` skips the menu debounce and is best for detector debugging.
`--dump-state-stable` uses the same stable-session path as the menu bar.

## History Retention

The history database stores:

- `snapshots`: timestamped active counts and per-tool counts.

Pruning deletes snapshots older than one year and downsamples snapshots older than
90 days to one row per minute using the median active count.

To run pruning daily via cron:

```cron
0 3 * * * /path/to/.venv/bin/overclocked --prune >> ~/.overclocked/prune.log 2>&1
```

## Development

```bash
ruff check .
ruff format . --check
pytest -q
```

The code is organized around a few small modules:

- `src/overclocked/detectors.py`: process/transcript detection and session enrichment.
- `src/overclocked/render.py`: SwiftBar menu rendering.
- `src/overclocked/storage.py`: SQLite migrations, snapshots, and pruning.
- `src/overclocked/aggregates.py`: today's peak/average/sparkline queries.
- `src/overclocked/config.py`: TOML config and path redaction.
- `scripts/overclocked.py`: SwiftBar streamable plugin wrapper.
- `scripts/build-menubar-app.sh`: native Raycast-launchable menu bar app builder.
