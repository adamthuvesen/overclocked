# overclocked

A macOS menu bar widget that shows how many AI coding agents (Claude Code, Cursor, Codex) are active on your machine at once. Use it to avoid AI brain fry.

```
🧠 3
```

Click to see per-session detail, today's peak/average, and a small sparkline.

## Install

```bash
# Clone and install in editable mode
git clone <repo>
cd overclocked
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

## SwiftBar Setup

[SwiftBar](https://github.com/swiftbar/SwiftBar) is required. Install it via Homebrew:

```bash
brew install swiftbar
```

Then symlink the plugin into your SwiftBar plugins directory:

```bash
# Find your SwiftBar plugins folder (SwiftBar > Preferences > Plugin Folder)
PLUGINS_DIR="$HOME/Library/Application Support/SwiftBar/Plugins"
ln -sf "$(pwd)/scripts/overclocked.5s.py" "$PLUGINS_DIR/overclocked.5s.py"
chmod +x "$PLUGINS_DIR/overclocked.5s.py"
```

Refresh SwiftBar. You should see `🧠 0` in your menu bar.

The plugin runs as a SwiftBar [streamable plugin](https://github.com/swiftbar/SwiftBar#plugin-types): SwiftBar spawns one long-lived `overclocked --stream` process, which emits a fresh menu every 5s. There is no per-tick Python startup, no per-tick SQLite reopen, and no subprocess timeout — SwiftBar restarts the process if it exits. Confirm one process is running with `pgrep -af "overclocked --stream"`.

`OVERCLOCKED_HOME` overrides the default runtime directory (`~/.overclocked/`).

### Native fallback

If SwiftBar is flaky on your macOS build, there's a tiny native AppKit fallback that renders `overclocked --once` directly in the menu bar without SwiftBar:

```bash
./scripts/build-menubar-app.sh
open ~/Applications/Overclocked.app
```

The build script now does two things every time you run it:

- builds a fresh app bundle under `.build/Overclocked.app`
- syncs that bundle into `~/Applications/Overclocked.app` so Spotlight launches the current version

If the native app was already running, the build script stops it, installs the updated bundle, and relaunches it automatically.

The app looks for `.venv/bin/overclocked` in the repo first. You can override that with `OVERCLOCKED_BIN=/path/to/overclocked open ~/Applications/Overclocked.app`.

## Privacy

Project folder names (e.g. `overclocked`, `dbt-transform`) are stored locally in `~/.overclocked/history.db`. To redact specific paths, create `~/.overclocked/config.toml`:

```toml
[privacy]
redact_paths = ["~/clients/", "~/personal/"]
```

Any session whose working directory starts with one of these prefixes will be stored and displayed as `redacted`.

## History

History is kept at `~/.overclocked/history.db`. To prune old data (run once a day, e.g. via cron or launchd):

```bash
overclocked --prune
```

### Cron entry

```
0 3 * * * /path/to/.venv/bin/overclocked --prune >> ~/.overclocked/prune.log 2>&1
```

### launchd plist

Create `~/Library/LaunchAgents/com.overclocked.prune.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.overclocked.prune</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/.venv/bin/overclocked</string>
        <string>--prune</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.overclocked.prune.plist
```

## Debug

```bash
overclocked --once                  # run one tick and print output to stdout
overclocked --stream --interval 5   # run the long-lived SwiftBar loop in the foreground
overclocked --dump-state            # print a JSON blob of current detection state
```

## Architecture

```
src/overclocked/
  config.py      — load ~/.overclocked/config.toml
  detectors.py   — pgrep-based active session detection
  identity.py    — resolve working directory and project label per PID
  storage.py     — SQLite persistence (snapshots + session events)
  aggregates.py  — today's peak, average, sparkline from history
  copy.py        — stateful witty one-liners
  render.py      — SwiftBar-formatted output strings
  cli.py         — entry point
```

No network calls. No telemetry. Everything stays in `~/.overclocked/`.
