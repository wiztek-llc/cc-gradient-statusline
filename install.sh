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

# ── Optional: real-time date/time awareness for Claude ───────────────────────
# Adds two hooks so Claude always knows the current date/time:
#   UserPromptSubmit → injects the date at the start of every turn
#   PostToolUse      → refreshes the time ~once/min during long tasks (throttled)
# Opt in interactively, or non-interactively via --with-time-hooks /
# --no-time-hooks / CC_INSTALL_TIME_HOOKS=1.
WITH_TIME="${CC_INSTALL_TIME_HOOKS:-}"
for arg in "$@"; do
  case "$arg" in
    --with-time-hooks) WITH_TIME=1 ;;
    --no-time-hooks)   WITH_TIME=0 ;;
  esac
done
if [ -z "$WITH_TIME" ]; then
  if [ -t 0 ]; then
    echo
    echo "→ Optional: give Claude real-time date/time awareness?"
    echo "  • the current date is injected at the start of every turn"
    echo "  • during long tasks, the time refreshes ~once a minute (throttled)"
    printf "  Enable this? [y/N] "
    read -r ans || ans=""
    case "$ans" in [Yy]*) WITH_TIME=1 ;; *) WITH_TIME=0 ;; esac
  else
    WITH_TIME=0   # non-interactive default: skip (use --with-time-hooks to force)
  fi
fi

if [ "$WITH_TIME" = "1" ]; then
  HOOKS_DEST="$HOME/.claude/hooks"
  mkdir -p "$HOOKS_DEST"
  cp "$SRC_DIR/hooks/inject-date.sh" "$SRC_DIR/hooks/inject-time.sh" "$HOOKS_DEST/"
  chmod +x "$HOOKS_DEST/inject-date.sh" "$HOOKS_DEST/inject-time.sh"
  echo "  ✓ copied time hooks → $HOOKS_DEST"
  HOOKS_DIR="$HOOKS_DEST" SETTINGS="$SETTINGS" python3 - <<'PY'
import json, os
settings = os.environ["SETTINGS"]
hooks_dir = os.environ["HOOKS_DIR"]
data = json.load(open(settings)) if os.path.isfile(settings) else {}
data.setdefault("hooks", {})

def ensure(event, entry, cmd):
    arr = data["hooks"].setdefault(event, [])
    for group in arr:                       # idempotent: skip if already present
        for h in group.get("hooks", []):
            if h.get("command") == cmd:
                return
    arr.append(entry)

date_cmd = hooks_dir + "/inject-date.sh"
time_cmd = hooks_dir + "/inject-time.sh"
ensure("UserPromptSubmit", {"hooks": [{"type": "command", "command": date_cmd}]}, date_cmd)
ensure("PostToolUse", {"matcher": "", "hooks": [{"type": "command", "command": time_cmd}]}, time_cmd)
with open(settings, "w") as f:
    json.dump(data, f, indent=2)
print("  ✓ settings.json hooks → UserPromptSubmit + PostToolUse")
PY
  echo "  • tune frequency with CC_TIME_INTERVAL=<seconds> (default 60)"
else
  echo "  · skipped time/date hooks (re-run with --with-time-hooks to add later)"
fi

echo
echo "Done. Open a new Claude Code session (or run /statusline) to see it."
echo "Themes: CC_STATUSLINE_THEME=pill|light|dark  ·  width: CC_BAR_WIDTH=12"
