#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Overclocked"
BUILD_DIR="$ROOT_DIR/.build/$APP_NAME.app"
INSTALL_ROOT="$HOME/Applications"
INSTALL_DIR="$INSTALL_ROOT/$APP_NAME.app"
MACOS_DIR="$BUILD_DIR/Contents/MacOS"
RESOURCES_DIR="$BUILD_DIR/Contents/Resources"
EXECUTABLE="$MACOS_DIR/$APP_NAME"
PROCESS_PATTERN='Overclocked.app/Contents/MacOS/Overclocked'

was_running=0
if pgrep -f "$PROCESS_PATTERN" >/dev/null 2>&1; then
  was_running=1
fi

pkill -f "$PROCESS_PATTERN" 2>/dev/null || true

mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

cat > "$BUILD_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>Overclocked</string>
  <key>CFBundleIdentifier</key>
  <string>app.overclocked</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Overclocked</string>
  <key>CFBundleDisplayName</key>
  <string>Overclocked</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>OverclockedRepoRoot</key>
  <string>__ROOT_DIR__</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
</dict>
</plist>
PLIST

/usr/bin/python3 -c '
from pathlib import Path
import sys

plist = Path(sys.argv[1])
root = sys.argv[2]
plist.write_text(plist.read_text().replace("__ROOT_DIR__", root))
' "$BUILD_DIR/Contents/Info.plist" "$ROOT_DIR"

xcrun swiftc \
  -O \
  -framework AppKit \
  "$ROOT_DIR/native/OverclockedMenuBar.swift" \
  -o "$EXECUTABLE"

mkdir -p "$INSTALL_ROOT"
rm -rf "$INSTALL_DIR"
cp -R "$BUILD_DIR" "$INSTALL_DIR"
/usr/bin/mdimport "$INSTALL_DIR" >/dev/null 2>&1 || true

if [[ "$was_running" -eq 1 ]]; then
  open "$INSTALL_DIR"
fi

echo "$INSTALL_DIR"
