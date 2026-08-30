#!/usr/bin/env bash
set -euo pipefail

SERVICEMENU="${HOME}/.local/share/kio/servicemenus/indir.desktop"
LEGACY_SERVICEMENU="${HOME}/.local/share/kio/servicemenus/ai-assistant.desktop"
LOCAL_BIN="${HOME}/.local/bin/indir"

echo "Uninstalling indir..."

[[ -f "$SERVICEMENU" ]] && rm -f "$SERVICEMENU" && echo "  Removed Dolphin menu"
[[ -f "$LEGACY_SERVICEMENU" ]] && rm -f "$LEGACY_SERVICEMENU" && echo "  Removed legacy Dolphin menu"
[[ -L "$LOCAL_BIN" ]] && rm -f "$LOCAL_BIN" && echo "  Removed CLI symlink"

echo ""
echo "Left in place (remove manually if desired):"
echo "  ~/.config/indir/config.toml (or legacy ~/.config/directory-ai/config.toml)"
echo "  .venv/ in project directory"
echo ""
echo "Done."
