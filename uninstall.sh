#!/usr/bin/env bash
# Remove cc-gradient-statusline and restore the previous statusLine setting.
set -euo pipefail
SETTINGS="$HOME/.claude/settings.json"
DEST="$HOME/.local/bin/cc-gradient-statusline.py"

if [ -f "$SETTINGS.bak" ]; then
  mv "$SETTINGS.bak" "$SETTINGS"
  echo "✓ restored settings.json from backup"
else
  echo "! no settings.json.bak found; leaving settings.json untouched"
  echo "  (manually remove the \"statusLine\" block if you want it gone)"
fi
rm -f "$DEST" && echo "✓ removed $DEST"
