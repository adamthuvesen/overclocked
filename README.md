# overclocked

A macOS menu bar widget that shows how many AI coding agents (Claude Code, Cursor, Codex) are active at once. Use to avoid AI brain fry.

Project rows default to a compact project-only display. Optional row decorations can show a short **status** (`working`, `waiting`, `done`) from transcript recency and process CPU, plus model/token hints where available. The menu bar count **includes all detected sessions** regardless of display settings.

## Install

```bash
git clone <repo>
cd overclocked
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## SwiftBar

Install [SwiftBar](https://github.com/swiftbar/SwiftBar), then symlink the plugin:

```bash
PLUGINS_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
ln -sf "$(pwd)/scripts/overclocked.5s.py" "$PLUGINS_DIR/overclocked.5s.py"
chmod +x "$PLUGINS_DIR/overclocked.5s.py"
```

Restart SwiftBar — you should see `👾 0` in your menu bar. The plugin polls every 5 seconds.

## Privacy

Project paths are stored locally in `~/.overclocked/history.db`. To redact specific directories, create `~/.overclocked/config.toml`:

```toml
[privacy]
redact_paths = ["~/clients/", "~/personal/"]

# Optional: show per-row status text in the dropdown (default: false).
[display]
session_status = false

# Optional: show model + token totals from Claude/Codex transcripts (default: false).
# Cursor rows intentionally stay metric-free.
session_metrics = false
```

## Pruning history

```bash
overclocked --prune
```

To run daily via cron:

```
0 3 * * * /path/to/.venv/bin/overclocked --prune >> ~/.overclocked/prune.log 2>&1
```

## Debug

```bash
overclocked --once                  # single tick to stdout
overclocked --stream --interval 5   # foreground stream loop
overclocked --dump-state            # JSON of current detection state
```

No network calls. No telemetry. Everything stays in `~/.overclocked/`.
