#!/usr/bin/env python3
"""
cc-gradient-statusline — a colorful, gradient Claude Code status line.

Reads Claude Code's stdin JSON payload (model, context, etc.) and fetches
real 5h / 7d usage limits from the Anthropic OAuth usage endpoint, then
renders smooth truecolor gradient progress bars with live reset countdowns.

Data source (real, verified):
  GET https://api.anthropic.com/api/oauth/usage
  -> { five_hour: {utilization, resets_at}, seven_day: {utilization, resets_at}, ... }

Usage:
  echo '<claude-code-json>' | statusline.py        # normal (called by Claude Code)
  statusline.py --demo                              # render with fake data, no network
  statusline.py --demo --pct5 87 --pct7 62          # demo at chosen utilization
  statusline.py --selftest                          # assertions on the rendering math
"""

import os
import sys
import json
import time
import subprocess
from datetime import datetime, timezone

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
CACHE = os.path.expanduser("~/.cache/cc-gradient-usage.json")
LOCK = os.path.expanduser("~/.cache/cc-gradient-usage.lock")
STDIN_LOG = os.path.expanduser("~/.cache/cc-gradient-stdin.json")
CREDS = os.environ.get("CC_CREDENTIALS", os.path.expanduser("~/.claude/.credentials.json"))
CACHE_TTL = int(os.environ.get("CC_CACHE_TTL", "45"))   # seconds usage stays fresh
LOCK_TTL = 25                                            # min seconds between API hits
BAR_W = int(os.environ.get("CC_BAR_WIDTH", "12"))       # bar width in cells

# Eighth-block characters for sub-cell fractional fill (smooth bars)
EIGHTHS = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"]

# Theme:
#   "pill"  — bright neon palette on a dark background "pill" (great on a cream
#             terminal: the dark island lets the neon colors pop). DEFAULT.
#   "light" — deep jewel tones, no background (native cream look).
#   "dark"  — bright neon on the terminal's own dark background.
# Override with CC_STATUSLINE_THEME=...
THEME = os.environ.get("CC_STATUSLINE_THEME", "pill").lower()

# Gradient color stops (position 0..1 -> RGB), heating toward red as usage rises.
# LIGHT: deep, saturated jewel tones that read on cream and end in dark blood-red.
# DARK:  brighter neon variant for dark terminals.
STOPS_LIGHT = [
    (0.00, (0, 158, 115)),    # deep emerald
    (0.22, (0, 150, 160)),    # deep teal
    (0.42, (0, 110, 190)),    # ocean blue
    (0.60, (78, 60, 200)),    # indigo
    (0.78, (150, 40, 165)),   # purple-magenta
    (0.90, (175, 28, 88)),    # crimson
    (1.00, (140, 16, 22)),    # dark blood red
]
STOPS_DARK = [
    (0.00, (0, 255, 170)),
    (0.25, (0, 229, 255)),
    (0.50, (90, 140, 255)),
    (0.72, (200, 90, 255)),
    (0.88, (255, 70, 180)),
    (1.00, (255, 60, 60)),
]
STOPS = STOPS_LIGHT if THEME == "light" else STOPS_DARK

# Text / chrome colors, theme-aware. BG_FILL is the pill background (or None).
if THEME == "light":
    TEXT = (38, 40, 50)        # model name — dark, reads on cream
    ACCENT = (0, 150, 160)     # diamond — deep teal
    GREY = (96, 99, 112)       # 5h / 7d glyphs
    SOFT = (124, 120, 116)     # ctx label, reset countdown
    TRACK = (181, 174, 162)    # empty bar cells — faint warm grey on cream
    SEPC = (200, 194, 182)
    BG_FILL = None
else:  # "pill" and "dark" share the neon foreground palette
    TEXT = (230, 232, 242)
    ACCENT = (0, 229, 255)
    GREY = (150, 152, 168)
    SOFT = (122, 124, 140)
    TRACK = (66, 70, 86)       # dim slate dots on the dark pill
    SEPC = (70, 74, 92)
    BG_FILL = (22, 23, 30) if THEME == "pill" else None  # the dark island

RESET = "\033[0m"
BGCODE = f"\033[48;2;{BG_FILL[0]};{BG_FILL[1]};{BG_FILL[2]}m" if BG_FILL else ""


def with_background(line):
    """Wrap the line in a dark pill: arm the bg, re-arm it after every reset
    (so fg resets don't punch holes in the background), pad the ends."""
    if not BG_FILL:
        return line
    armed = line.replace("\033[0m", "\033[0m" + BGCODE)
    return BGCODE + "  " + armed + "  \033[0m"


# ----------------------------------------------------------------------------
# Color helpers
# ----------------------------------------------------------------------------
def lerp(a, b, t):
    return a + (b - a) * t


def grad(pos):
    """Sample the gradient palette at pos in [0,1] -> (r,g,b)."""
    pos = max(0.0, min(1.0, pos))
    for i in range(len(STOPS) - 1):
        p0, c0 = STOPS[i]
        p1, c1 = STOPS[i + 1]
        if p0 <= pos <= p1:
            t = 0 if p1 == p0 else (pos - p0) / (p1 - p0)
            return tuple(round(lerp(c0[j], c1[j], t)) for j in range(3))
    return STOPS[-1][1]


def fg(rgb):
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"


def dim(rgb, f=0.32):
    return tuple(round(c * f) for c in rgb)


def hexstr(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


# ----------------------------------------------------------------------------
# Bar rendering
# ----------------------------------------------------------------------------
def gradient_bar(pct, width=BAR_W):
    """
    Render a smooth gradient bar for pct in [0,100].
    Each cell is colored by the gradient at its own position along the FULL bar,
    so the leading edge 'heats up' (cyan -> pink -> red) as utilization rises.
    Empty track cells are a dim version of the gradient at that position.
    """
    pct = max(0.0, min(100.0, float(pct)))
    filled = (pct / 100.0) * width            # fractional number of filled cells
    out = []
    for i in range(width):
        cell_pos = (i + 0.5) / width          # this cell's location along the bar
        color = grad(cell_pos)
        empty = TRACK if TRACK is not None else dim(color)
        if i < int(filled):
            out.append(fg(color) + "█")
        elif i == int(filled):
            frac = filled - int(filled)
            ch = EIGHTHS[round(frac * 8)]
            if ch == " ":
                out.append(fg(empty) + "·")
            else:
                out.append(fg(color) + ch)
        else:
            out.append(fg(empty) + "·")
    return "".join(out) + RESET


def pct_label(pct):
    """Percentage colored by its own severity (the gradient at that pct)."""
    c = grad(pct / 100.0)
    return fg(c) + f"{round(pct):>2d}%" + RESET


# ----------------------------------------------------------------------------
# Time helpers
# ----------------------------------------------------------------------------
def parse_iso(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def countdown(resets_at, now=None):
    """'2h13m' / '45m' / '3d4h' until reset, or '' if unknown.
    Accepts an ISO-8601 string or a Unix epoch (int/float)."""
    if isinstance(resets_at, (int, float)):
        dt = datetime.fromtimestamp(resets_at, tz=timezone.utc)
    elif isinstance(resets_at, str):
        dt = parse_iso(resets_at)
    else:
        dt = None
    if dt is None:
        return ""
    now = now or datetime.now(timezone.utc)
    secs = int((dt - now).total_seconds())
    if secs <= 0:
        return "now"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    if d > 0:
        return f"{d}d{h}h"
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"


# ----------------------------------------------------------------------------
# Usage data (cached) from the OAuth endpoint
# ----------------------------------------------------------------------------
def get_token():
    try:
        if os.path.isfile(CREDS):
            with open(CREDS) as f:
                tok = json.load(f).get("claudeAiOauth", {}).get("accessToken")
                if tok:
                    return tok
    except Exception:
        pass
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return json.loads(out.stdout).get("claudeAiOauth", {}).get("accessToken")
    except Exception:
        pass
    return None


def fetch_usage():
    token = get_token()
    if not token:
        return None
    try:
        out = subprocess.run(
            ["curl", "-s", "--max-time", "6",
             "https://api.anthropic.com/api/oauth/usage",
             "-H", f"Authorization: Bearer {token}",
             "-H", "anthropic-beta: oauth-2025-04-20"],
            capture_output=True, text=True, timeout=8,
        )
        data = json.loads(out.stdout)
        if "five_hour" in data:
            return data
    except Exception:
        pass
    return None


def cached_usage():
    """Return usage JSON, using a short TTL cache + lock to be fast and polite."""
    now = time.time()
    # ensure the cache dir exists (fresh machines may lack ~/.cache)
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    except Exception:
        pass
    if os.path.isfile(CACHE):
        age = now - os.path.getmtime(CACHE)
        if age < CACHE_TTL:
            try:
                with open(CACHE) as f:
                    return json.load(f)
            except Exception:
                pass
    # avoid stampeding the API: if a fetch happened very recently, reuse stale cache
    if os.path.isfile(LOCK) and (now - os.path.getmtime(LOCK)) < LOCK_TTL:
        if os.path.isfile(CACHE):
            try:
                with open(CACHE) as f:
                    return json.load(f)
            except Exception:
                pass
    try:
        open(LOCK, "w").close()
    except Exception:
        pass
    data = fetch_usage()
    if data:
        try:
            with open(CACHE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass
        return data
    # fall back to stale cache on failure
    if os.path.isfile(CACHE):
        try:
            with open(CACHE) as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ----------------------------------------------------------------------------
# Compose the status line
# ----------------------------------------------------------------------------
def usage_from_payload(payload):
    """Claude Code delivers rate_limits natively in stdin (epoch reset times).
    Prefer it — instant, no network. Returns the same shape as the OAuth API."""
    rl = payload.get("rate_limits") or {}
    fh, sd = rl.get("five_hour"), rl.get("seven_day")
    if not fh and not sd:
        return None
    def conv(w):
        if not w:
            return None
        return {"utilization": w.get("used_percentage", 0),
                "resets_at": w.get("resets_at")}
    return {"five_hour": conv(fh), "seven_day": conv(sd)}


def model_chip(payload):
    name = (
        payload.get("model", {}).get("display_name")
        or payload.get("model", {}).get("id")
        or "Claude"
    )
    return fg(ACCENT) + "◆ " + RESET + fg(TEXT) + name + RESET


def ctx_chip(payload):
    cw = payload.get("context_window", {}) or {}
    used = cw.get("used_percentage")
    if used is None:
        ti = cw.get("total_input_tokens")
        size = cw.get("context_window_size")
        if ti and size:
            used = 100.0 * ti / size
    if used is None:
        return ""
    c = grad(min(used, 100) / 100.0)
    return fg(SOFT) + "ctx " + RESET + fg(c) + f"{round(used)}%" + RESET


def meter(glyph, window):
    """One labeled gradient meter with countdown."""
    if not window:
        return None
    pct = window.get("utilization", 0.0)
    bar = gradient_bar(pct)
    lbl = pct_label(pct)
    cd = countdown(window.get("resets_at"))
    tail = fg(SOFT) + f" ↺{cd}" + RESET if cd else ""
    return fg(GREY) + glyph + " " + RESET + bar + " " + lbl + tail


def build_line(payload, usage):
    sep = fg(SEPC) + "  " + RESET
    parts = [model_chip(payload)]
    cc = ctx_chip(payload)
    if cc:
        parts.append(cc)
    if usage:
        m5 = meter("5h", usage.get("five_hour"))
        m7 = meter("7d", usage.get("seven_day"))
        if m5:
            parts.append(m5)
        if m7:
            parts.append(m7)
    else:
        parts.append(fg(SOFT) + "limits unavailable" + RESET)
    return with_background(sep.join(parts))


# ----------------------------------------------------------------------------
# Entry
# ----------------------------------------------------------------------------
def read_payload():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        data = json.loads(raw)
        try:
            with open(STDIN_LOG, "w") as f:
                f.write(raw)
        except Exception:
            pass
        return data
    except Exception:
        return {}


def main():
    args = sys.argv[1:]

    if "--selftest" in args:
        run_selftest()
        return

    if "--demo" in args:
        def argval(flag, default):
            return float(args[args.index(flag) + 1]) if flag in args else default
        p5 = argval("--pct5", 34.0)
        p7 = argval("--pct7", 16.0)
        usage = {
            "five_hour": {"utilization": p5, "resets_at": "2026-06-07T21:00:00+00:00"},
            "seven_day": {"utilization": p7, "resets_at": "2026-06-13T03:00:00+00:00"},
        }
        payload = {"model": {"display_name": "Opus 4.8"},
                   "context_window": {"used_percentage": 42}}
        print(build_line(payload, usage))
        return

    payload = read_payload()
    # Prefer the rate_limits Claude Code ships in stdin (instant, offline-safe);
    # fall back to the OAuth usage endpoint only if the payload lacks them.
    usage = usage_from_payload(payload) or cached_usage()
    print(build_line(payload, usage))


def run_selftest():
    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and cond
        print(("PASS " if cond else "FAIL ") + name)

    # gradient endpoints + hue progression (cool -> hot), palette-agnostic
    lo, hi = grad(0.05), grad(0.98)
    check("low usage is cool (not red-dominant)", lo[0] < max(lo[1], lo[2]))
    check("high usage is hot (red-dominant)", hi[0] > hi[1] and hi[0] > hi[2])
    check("grad clamps <0", grad(-1) == STOPS[0][1])
    check("grad clamps >1", grad(2) == STOPS[-1][1])
    # redder as it heats: red channel at 98% exceeds red channel at 5%
    check("red rises into the limit", grad(0.98)[0] > grad(0.05)[0])

    # bar fill: count full blocks scales with pct
    def fullblocks(pct):
        return gradient_bar(pct, 10).count("█")
    check("0% has 0 full blocks", fullblocks(0) == 0)
    check("100% has 10 full blocks", fullblocks(100) == 10)
    check("50% has ~5 full blocks", fullblocks(50) == 5)
    check("more usage => more fill", fullblocks(80) > fullblocks(30))

    # countdown math
    now = datetime(2026, 6, 7, 19, 0, 0, tzinfo=timezone.utc)
    check("2h countdown", countdown("2026-06-07T21:00:00+00:00", now) == "2h00m")
    check("multiday countdown", countdown("2026-06-13T03:00:00+00:00", now) == "5d8h")
    check("past => now", countdown("2026-06-07T18:00:00+00:00", now) == "now")
    check("bad input => empty", countdown("not-a-date", now) == "")

    # full line builds without raising and contains truecolor codes
    line = build_line({"model": {"display_name": "Opus 4.8"}},
                      {"five_hour": {"utilization": 34, "resets_at": "2026-06-07T21:00:00+00:00"},
                       "seven_day": {"utilization": 16, "resets_at": "2026-06-13T03:00:00+00:00"}})
    check("line emits truecolor", "\033[38;2;" in line)
    check("line shows reset glyph", "↺" in line)

    print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
