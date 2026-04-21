# overclocked

A macOS menu bar widget that shows how many AI coding agents (Claude Code, Cursor, Codex) are active at once. Use to avoid AI brain fry.

```
🧠 3
```

Project rows show a short **status** (`working`, `waiting`, `done`) from transcript recency and process CPU (same idea as the abtop TUI). The menu bar count **includes all detected sessions** regardless of status.

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

Refresh SwiftBar — you should see `🧠 0` in your menu bar.

### Native fallback

If SwiftBar is flaky, there's a native AppKit alternative:

```bash
./scripts/build-menubar-app.sh
open ~/Applications/Overclocked.app
```

## Privacy

Project paths are stored locally in `~/.overclocked/history.db`. To redact specific directories, create `~/.overclocked/config.toml`:

```toml
[privacy]
redact_paths = ["~/clients/", "~/personal/"]

# Optional: show model + token totals from Claude/Codex transcripts (default: true).
# Set false to hide these hints in the menu and omit them from --dump-state.
[display]
session_metrics = true
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
