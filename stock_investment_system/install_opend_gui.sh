#!/usr/bin/env bash
set -euo pipefail

SYSTEM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DMG_PATH="$SYSTEM_DIR/vendor/Futu_OpenD_10.8.6808_Mac/Futu_OpenD-GUI_10.8.6808_Mac/Futu_OpenD-GUI_10.8.6808_Mac.dmg"

if [[ ! -f "$DMG_PATH" ]]; then
  echo "DMG not found: $DMG_PATH" >&2
  exit 1
fi

VOLUME_PATH="$(hdiutil attach "$DMG_PATH" -nobrowse -readonly | awk -F '\t' '/\/Volumes\// {print $NF; exit}')"
if [[ -z "$VOLUME_PATH" ]]; then
  echo "Failed to mount DMG: $DMG_PATH" >&2
  exit 1
fi

cleanup() {
  hdiutil detach "$VOLUME_PATH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

APP_IN_DMG="$(find "$VOLUME_PATH" -maxdepth 1 -name "*.app" -type d | head -1)"
if [[ -z "$APP_IN_DMG" ]]; then
  echo "No GUI app found in mounted DMG: $VOLUME_PATH" >&2
  exit 1
fi

APP_NAME="$(basename "$APP_IN_DMG")"
TARGET_APP="/Applications/$APP_NAME"

ditto "$APP_IN_DMG" "$TARGET_APP"
xattr -rd com.apple.quarantine "$TARGET_APP" 2>/dev/null || true

VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$TARGET_APP/Contents/Info.plist" 2>/dev/null || true)"
echo "Installed $APP_NAME $VERSION to /Applications"
