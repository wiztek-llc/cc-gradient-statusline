# cc-gradient-statusline

A colorful, gradient Claude Code status line showing live **5-hour** and
**7-day** usage limits with smooth truecolor bars and reset countdowns.

```
◆ Opus 4.8   ctx 9%   5h ████▏······· 34% ↺21m   7d █▉·········· 16% ↺5d6h
```

Each bar fills with a deep, saturated gradient that **heats up** as you
approach the limit: emerald → teal → ocean blue → indigo → purple → magenta →
dark red. The percentage number is colored by the same severity, so a glance
tells you how close you are to a wall.

## How it works

Claude Code runs the `statusLine` command after each turn and pipes a JSON
payload to its stdin. As of recent versions that payload includes a
`rate_limits` block:

```json
"rate_limits": {
  "five_hour": { "used_percentage": 34, "resets_at": 1780866000 },
  "seven_day": { "used_percentage": 16, "resets_at": 1781319600 }
}
```

The script reads those **directly from stdin** (instant, offline-safe). If a
payload ever lacks them, it falls back to the Anthropic OAuth usage endpoint
(`GET /api/oauth/usage`, cached ~45s) using the local OAuth token.

## Install

```bash
git clone <repo-url> cc-gradient-statusline
cd cc-gradient-statusline
./install.sh
```

The installer copies `statusline.py` to `~/.local/bin/` and points your
`~/.claude/settings.json` `statusLine` at it (backing up the old settings to
`settings.json.bak`). Open a new Claude Code session to see it. To revert,
run `./uninstall.sh`.

Manual install, if you prefer:

```bash
cp statusline.py ~/.local/bin/cc-gradient-statusline.py
chmod +x ~/.local/bin/cc-gradient-statusline.py
```

then in `~/.claude/settings.json`:

```json
"statusLine": {
  "type": "command",
  "command": "~/.local/bin/cc-gradient-statusline.py"
}
```

## Requirements

- **Claude Code** with a Pro/Max (Claude.ai) subscription — the 5h/7d limits
  come from your account. Recent Claude Code ships them in the status-line
  payload; older versions fall back to the Anthropic usage endpoint using
  your local OAuth token (macOS reads the Keychain; Linux/elsewhere reads
  `~/.claude/.credentials.json`).
- **Python 3** (stdlib only — no pip installs).
- A terminal with truecolor + a monospace font that has block glyphs
  (`█ ▏▎▍`) and `◆ ↺`. Most modern fonts qualify (Maple Mono, JetBrains
  Mono, MesloLGS NF, SF Mono, …).

## Tweak

Environment variables:

| Var | Default | Meaning |
|-----|---------|---------|
| `CC_STATUSLINE_THEME` | `pill` | `pill` = bright neon on a dark background pill (pops on a cream/light terminal); `light` = deep jewel tones, no background; `dark` = neon on the terminal's own dark bg |
| `CC_BAR_WIDTH` | `12` | bar width in cells |
| `CC_CACHE_TTL` | `45` | seconds the OAuth-fallback usage stays cached |

The pill background color is `BG_FILL` in the theme block (default `(22,23,30)`).

Gradient stops live in `STOPS_LIGHT` / `STOPS_DARK` near the top of the file —
edit the RGB tuples to recolor.

## Verify / preview

```bash
python3 statusline.py --selftest                 # 15 assertions on the math
python3 statusline.py --demo --pct5 95 --pct7 78 # render at chosen utilization
python3 showcase.py                              # /tmp/showcase.png across the full range
```

`render_png.py` faithfully rasterizes the ANSI output to a PNG (used by
`showcase.py`); set `CC_RENDER_BG="r,g,b"` to match your terminal background.
