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
CACHE_TTL = int(os.environ.get("CC_CACHE_TTL", "15"))   # seconds usage stays fresh
LOCK_TTL = 12                                            # min seconds between API hits
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
    SAVED = (0, 150, 90)       # ledger tokens-saved — deep green, reads on cream
    BG_FILL = None
else:  # "pill" and "dark" share the neon foreground palette
    TEXT = (230, 232, 242)
    ACCENT = (0, 229, 255)
    GREY = (150, 152, 168)
    SOFT = (122, 124, 140)
    TRACK = (66, 70, 86)       # dim slate dots on the dark pill
    SEPC = (70, 74, 92)
    SAVED = (0, 230, 150)      # ledger tokens-saved — mint, pops on the pill
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
    m, s = divmod(rem, 60)
    # Days away (e.g. the 7d window): coarse, seconds are meaningless.
    if d > 0:
        return f"{d}d{h}h"
    # Under a day (e.g. the 5h window): a live ticking clock, updates per second.
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


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


def read_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except Exception:
        return None


def spawn_refresh():
    """Kick off a detached background fetch (no waiting) to refresh the shared
    cache. A lock throttles it so that, across ALL sessions, at most one fetch
    runs per ~LOCK_TTL — and no status-line render ever blocks on the network."""
    now = time.time()
    if os.path.isfile(LOCK) and (now - os.path.getmtime(LOCK)) < LOCK_TTL:
        return  # a refresh started recently (possibly by another session)
    try:
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        open(LOCK, "w").close()
        subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "--refresh-usage"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except Exception:
        pass


def do_refresh():
    """Background worker: fetch account-global usage and write the shared cache."""
    data = fetch_usage()
    if data:
        try:
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, "w") as f:
                json.dump(data, f)
        except Exception:
            pass


def cached_usage():
    """Account-global usage from the shared cache. If the cache is stale, trigger
    a non-blocking background refresh and return whatever we have right now
    (slightly stale, or None on a cold machine). Because the cache file is shared
    across every session, all sessions display the SAME numbers."""
    data = read_cache()
    fresh = False
    if os.path.isfile(CACHE):
        fresh = (time.time() - os.path.getmtime(CACHE)) < CACHE_TTL
    if not fresh:
        spawn_refresh()
    return data


# ----------------------------------------------------------------------------
# Compose the status line
# ----------------------------------------------------------------------------
def usage_from_payload(payload):
    """COLD-START FALLBACK ONLY. Claude Code ships rate_limits in stdin, but that
    is a per-session snapshot frozen at the session's last API response — so it
    disagrees across sessions. We only use it for the very first paint before the
    shared account-global cache is warm; never written to the shared cache."""
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


_ANSI_RE = None


def visible_len(s):
    """On-screen width of a string: ANSI escapes stripped, chars counted.
    All glyphs we use (block elements, ◆, ↺, digits) are monospace width 1."""
    global _ANSI_RE
    if _ANSI_RE is None:
        import re
        _ANSI_RE = re.compile(r"\033\[[0-9;]*m")
    return len(_ANSI_RE.sub("", s))


def _ioctl_cols(path):
    """Columns of a tty device via TIOCGWINSZ; non-blocking open, never hangs."""
    fd = None
    try:
        import fcntl, termios, struct
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        hw = struct.unpack("hhhh", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
        return hw[1] or None
    except Exception:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass


def term_cols():
    """Live width of the pane. Claude Code passes no width, and the status-line
    process has no tty of its own, so we read the winsize of the nearest
    ancestor's controlling tty (the pty Claude Code runs in, which cmux resizes
    on every pane resize). One process-table snapshot is read and walked in
    memory — a single subprocess, no stale per-pid cache files. Returns None if
    no ancestor tty is found (caller then renders the full, widest layout)."""
    env = os.environ.get("CC_STATUSLINE_COLS")
    if env:
        try:
            return int(env)
        except Exception:
            pass
    # Targeted per-pid queries only — `ps -A` (whole table) can take seconds on a
    # busy machine, whereas `ps -p PID` is ~10ms. Start at the parent (the status
    # line process itself never owns a tty — stdin/stdout are pipes), so the
    # common case is a single ps call: parent = Claude Code, which owns the pty.
    pid = str(os.getppid())
    for _ in range(12):
        try:
            out = subprocess.check_output(
                ["ps", "-o", "ppid=,tty=", "-p", pid],
                text=True, stderr=subprocess.DEVNULL).split(None, 1)
        except Exception:
            break
        if not out:
            break
        ppid = out[0]
        tty = out[1].strip() if len(out) > 1 else ""
        if tty and tty not in ("??", "-", "?"):
            c = _ioctl_cols("/dev/" + tty)
            if c:
                return c
        if not ppid or ppid in ("0", "1"):
            break
        pid = ppid
    return None


def countdown_short(resets_at, now=None):
    """Ultra-compact reset hint for narrow widths: 5d / 4h / 12m."""
    cd = countdown(resets_at, now)
    if not cd or cd == "now":
        return cd
    if "d" in cd:
        return cd.split("d")[0] + "d"
    # cd is H:MM:SS or MM:SS
    parts = cd.split(":")
    if len(parts) == 3:
        return parts[0] + "h"
    return parts[0] + "m"


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


def _human(n):
    if n >= 1_000_000:
        return f"{n / 1e6:.1f}M"
    if n >= 1_000:
        return f"{n / 1e3:.0f}k"
    return str(int(n))


def _find_ledger_stats(start):
    """Walk up from `start` to find a Context Ledger stats file for this project."""
    try:
        d = os.path.abspath(start)
    except Exception:
        return None
    while True:
        p = os.path.join(d, ".claude", "context-ledger.stats.json")
        if os.path.isfile(p):
            return p
        nd = os.path.dirname(d)
        if nd == d:
            return None
        d = nd


def savings_chip(payload):
    """Show cumulative context tokens saved by Context Ledger for this project."""
    cwd = (payload.get("workspace", {}) or {}).get("current_dir") or payload.get("cwd")
    if not cwd:
        return ""
    p = _find_ledger_stats(cwd)
    if not p:
        return ""
    try:
        saved = json.load(open(p)).get("saved_tokens", 0)
    except Exception:
        return ""
    if saved <= 0:
        return ""
    return (fg(SAVED) + "⊟ " + _human(saved) + RESET
            + fg(SOFT) + " ctx saved" + RESET)


def meter(glyph, window, bar_w=BAR_W, cd_mode="full", show_label=True):
    """One gradient meter. Parametric so the layout can shed detail under width
    pressure: bar_w=0 drops the bar, cd_mode in full|short|none, show_label
    toggles the 5h/7d tag."""
    if not window:
        return None
    pct = window.get("utilization", 0.0)
    out = ""
    if show_label:
        out += fg(GREY) + glyph + " " + RESET
    if bar_w > 0:
        out += gradient_bar(pct, bar_w) + " "
    out += pct_label(pct)
    if cd_mode == "full":
        cd = countdown(window.get("resets_at"))
    elif cd_mode == "short":
        cd = countdown_short(window.get("resets_at"))
    else:
        cd = ""
    if cd:
        out += fg(SOFT) + " ↺" + cd + RESET
    return out


def render(payload, usage, model="full", ctx=True, bar_w=BAR_W,
           cd="full", labels=True, saved=True):
    """Render one layout variant given the detail flags."""
    sep = fg(SEPC) + "  " + RESET
    parts = []
    if model == "full":
        parts.append(model_chip(payload))
    elif model == "icon":
        parts.append(fg(ACCENT) + "◆" + RESET)
    if ctx:
        cc = ctx_chip(payload)
        if cc:
            parts.append(cc)
    if saved:
        sc = savings_chip(payload)
        if sc:
            parts.append(sc)
    if usage:
        for glyph, key in (("5h", "five_hour"), ("7d", "seven_day")):
            m = meter(glyph, usage.get(key), bar_w, cd, labels)
            if m:
                parts.append(m)
    else:
        parts.append(fg(SOFT) + "limits unavailable" + RESET)
    return with_background(sep.join(parts))


# Layout tiers, richest → leanest. The first whose on-screen width fits the
# pane is used, so detail is shed in priority order (model name → ctx →
# countdowns → bars → 5h/7d labels) and the percentages survive longest.
TIERS = [
    dict(model="full", ctx=True,  bar_w=12, cd="full",  labels=True,  saved=True),
    dict(model="full", ctx=False, bar_w=12, cd="full",  labels=True,  saved=True),
    dict(model="icon", ctx=False, bar_w=12, cd="full",  labels=True,  saved=True),
    dict(model="icon", ctx=False, bar_w=10, cd="short", labels=True,  saved=True),
    dict(model="none", ctx=False, bar_w=8,  cd="short", labels=True,  saved=False),
    dict(model="none", ctx=False, bar_w=6,  cd="none",  labels=True,  saved=False),
    dict(model="none", ctx=False, bar_w=0,  cd="none",  labels=True,  saved=False),
    dict(model="none", ctx=False, bar_w=0,  cd="none",  labels=False, saved=False),
]


def build_line(payload, usage, cols=None):
    """Pick the richest layout tier that fits `cols`. Falls back to full when
    width is unknown, and to the leanest tier when nothing fits (Claude Code
    then truncates, but the most important info is leftmost)."""
    if not cols or cols <= 0:
        return render(payload, usage, **TIERS[0])
    budget = cols - 1  # small safety margin against off-by-one truncation
    for tier in TIERS:
        line = render(payload, usage, **tier)
        if visible_len(line) <= budget:
            return line
    return render(payload, usage, **TIERS[-1])


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

    if "--refresh-usage" in args:
        do_refresh()
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
        cols = int(argval("--cols", 0)) or None
        print(build_line(payload, usage, cols))
        return

    payload = read_payload()
    # Account-global usage from the shared cache (same numbers in every session).
    # Only if the cache is cold do we fall back to the per-session stdin snapshot
    # for the first paint; the background refresh warms the cache within ~1s.
    usage = cached_usage() or usage_from_payload(payload)
    print(build_line(payload, usage, term_cols()))


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

    # countdown math — sub-day is a live H:MM:SS / MM:SS ticking clock
    now = datetime(2026, 6, 7, 19, 0, 0, tzinfo=timezone.utc)
    check("hours -> H:MM:SS", countdown("2026-06-07T21:03:45+00:00", now) == "2:03:45")
    check("sub-hour -> MM:SS", countdown("2026-06-07T19:45:30+00:00", now) == "45:30")
    check("ticks each second", countdown("2026-06-07T21:00:02+00:00", now) == "2:00:02")
    check("multiday stays coarse", countdown("2026-06-13T03:00:00+00:00", now) == "5d8h")
    check("epoch input works", countdown(int(now.timestamp()) + 65, now) == "1:05")
    check("past => now", countdown("2026-06-07T18:00:00+00:00", now) == "now")
    check("bad input => empty", countdown("not-a-date", now) == "")

    # full line builds without raising and contains truecolor codes
    line = build_line({"model": {"display_name": "Opus 4.8"}},
                      {"five_hour": {"utilization": 34, "resets_at": "2026-06-07T21:00:00+00:00"},
                       "seven_day": {"utilization": 16, "resets_at": "2026-06-13T03:00:00+00:00"}})
    check("line emits truecolor", "\033[38;2;" in line)
    check("line shows reset glyph", "↺" in line)

    # responsive auto-compaction
    pl = {"model": {"display_name": "Opus 4.8"}, "context_window": {"used_percentage": 42}}
    us = {"five_hour": {"utilization": 34, "resets_at": "2026-06-07T21:00:00+00:00"},
          "seven_day": {"utilization": 16, "resets_at": "2026-06-13T03:00:00+00:00"}}
    check("visible_len strips ANSI", visible_len("\033[38;2;1;2;3mAB\033[0m") == 2)
    wide = build_line(pl, us, 200)
    check("wide keeps model name", "Opus 4.8" in wide)
    # every width from 6..120 must produce a line that fits (or the leanest tier)
    fits_all = True
    prev = 0
    for c in range(120, 5, -1):
        vl = visible_len(build_line(pl, us, c))
        if c >= 12 and vl > c - 1 and vl > visible_len(render(pl, us, **TIERS[-1])):
            fits_all = False
            break
    check("every width >=12 fits its budget", fits_all)
    narrow = build_line(pl, us, 24)
    check("narrow drops model name", "Opus 4.8" not in narrow)
    check("narrow still shows a percentage", "%" in narrow)
    check("wide is wider than narrow", visible_len(wide) > visible_len(narrow))
    check("monotonic: 40c <= 80c width",
          visible_len(build_line(pl, us, 40)) <= visible_len(build_line(pl, us, 80)))

    print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
