#!/usr/bin/env bash
#
# Install cc-gradient-statusline into Claude Code.
#   - copies statusline.py to ~/.local/bin/cc-gradient-statusline.py
#   - points ~/.claude/settings.json "statusLine" at it (backing up first)
#
# Safe to re-run. Reads YOUR own Claude credentials at runtime; no secrets here.

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
DEST="$BIN_DIR/cc-gradient-statusline.py"
SETTINGS="$HOME/.claude/settings.json"

echo "→ Installing cc-gradient-statusline"

command -v python3 >/dev/null 2>&1 || { echo "✗ python3 not found (required)"; exit 1; }

mkdir -p "$BIN_DIR" "$HOME/.claude"
cp "$SRC_DIR/statusline.py" "$DEST"
chmod +x "$DEST"
echo "  ✓ copied script → $DEST"

# Sanity check the script runs (offline payload: no network/keychain touched)
PROBE='{"model":{"display_name":"Claude"},"rate_limits":{"five_hour":{"used_percentage":50,"resets_at":0},"seven_day":{"used_percentage":50,"resets_at":0}}}'
echo "$PROBE" | "$DEST" >/dev/null 2>&1 && echo "  ✓ script executes" || { echo "✗ script failed to run"; exit 1; }

# Patch settings.json with python3 (preserves all other keys; creates file if missing)
DEST="$DEST" SETTINGS="$SETTINGS" python3 - <<'PY'
import json, os, shutil, sys
settings = os.environ["SETTINGS"]
dest = os.environ["DEST"]
data = {}
if os.path.isfile(settings):
    if not os.path.exists(settings + ".bak"):   # keep the true original
        shutil.copy(settings, settings + ".bak")
    try:
        with open(settings) as f:
            data = json.load(f)
    except Exception:
        print("  ! existing settings.json wasn't valid JSON; backed up to .bak, writing fresh")
        data = {}
data["statusLine"] = {"type": "command", "command": dest}
with open(settings, "w") as f:
    json.dump(data, f, indent=2)
print("  ✓ settings.json statusLine → " + dest)
PY

echo
echo "Done. Open a new Claude Code session (or run /statusline) to see it."
echo "Themes: CC_STATUSLINE_THEME=pill|light|dark  ·  width: CC_BAR_WIDTH=12"
