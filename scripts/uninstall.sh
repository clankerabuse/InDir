#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICEMENU="${HOME}/.local/share/kio/servicemenus/indir.desktop"
LEGACY_SERVICEMENU="${HOME}/.local/share/kio/servicemenus/ai-assistant.desktop"
LOCAL_BIN="${HOME}/.local/bin/indir"
APP_DESKTOP="${HOME}/.local/share/applications/indir.desktop"
ICON_SVG="${HOME}/.local/share/icons/hicolor/scalable/apps/indir.svg"

echo "Uninstalling indir..."

[[ -f "$SERVICEMENU" ]] && rm -f "$SERVICEMENU" && echo "  Removed Dolphin menu"
[[ -f "$LEGACY_SERVICEMENU" ]] && rm -f "$LEGACY_SERVICEMENU" && echo "  Removed legacy Dolphin menu"
[[ -f "$APP_DESKTOP" ]] && rm -f "$APP_DESKTOP" && echo "  Removed application launcher entry"
[[ -f "$ICON_SVG" ]] && rm -f "$ICON_SVG" && echo "  Removed application icon"
for size in 16 24 32 48 64 128 256; do
    png="${HOME}/.local/share/icons/hicolor/${size}x${size}/apps/indir.png"
    [[ -f "$png" ]] && rm -f "$png"
done
python3 "$SCRIPT_DIR/thunar_uca.py" remove
[[ -L "$LOCAL_BIN" ]] && rm -f "$LOCAL_BIN" && echo "  Removed CLI symlink"

echo ""
echo "Left in place (remove manually if desired):"
echo "  ~/.config/indir/config.toml (or legacy ~/.config/directory-ai/config.toml)"
echo "  .venv/ in project directory"
echo ""
echo "Done."
