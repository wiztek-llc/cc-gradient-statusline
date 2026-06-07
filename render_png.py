#!/usr/bin/env python3
"""
Render an ANSI-truecolor string to a PNG that faithfully reproduces what a
terminal draws. Used to visually verify the gradient status line.

  cat ansi.txt | python3 render_png.py out.png
"""
import sys
import re
from PIL import Image, ImageDraw, ImageFont

ANSI = re.compile(r"\033\[([0-9;]*)m")
import os as _os
_bg = _os.environ.get("CC_RENDER_BG", "233,229,221")   # cream by default
BG = tuple(int(x) for x in _bg.split(","))
DEFAULT_FG = (38, 40, 50)

# A few monospace candidates that include block + arrow glyphs on macOS.
FONT_CANDIDATES = [
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Monaco.ttf",
    "/Library/Fonts/Menlo.ttc",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def parse(text):
    """Yield (char, fg, bg) honoring \033[38;2..m, \033[48;2..m and reset.
    bg is None when default (page background)."""
    cur = DEFAULT_FG
    curbg = None
    i = 0
    out = []
    while i < len(text):
        m = ANSI.match(text, i)
        if m:
            params = m.group(1)
            nums = [int(x) for x in params.split(";") if x != ""] or [0]
            if nums[:2] == [38, 2] and len(nums) >= 5:
                cur = (nums[2], nums[3], nums[4])
            elif nums[:2] == [48, 2] and len(nums) >= 5:
                curbg = (nums[2], nums[3], nums[4])
            elif nums == [0]:
                cur = DEFAULT_FG
                curbg = None
            i = m.end()
            continue
        ch = text[i]
        if ch != "\n":
            out.append((ch, cur, curbg))
        i += 1
    return out


def render(ansi_text, out_path, scale=3, size=16, pad=14):
    cells = parse(ansi_text.rstrip("\n"))
    font = load_font(size * scale)
    # measure cell width with a full block (monospace => constant advance)
    probe = Image.new("RGB", (10, 10))
    d0 = ImageDraw.Draw(probe)
    bb = d0.textbbox((0, 0), "█", font=font)
    cw = bb[2] - bb[0]
    ch_h = (bb[3] - bb[1])
    line_h = int(size * scale * 1.6)
    W = pad * 2 * scale + cw * len(cells) + cw  # a little slack
    H = pad * 2 * scale + line_h
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    x = pad * scale
    y = pad * scale
    # extend the pill bg a little above/below the text for a clean rectangle
    yb0, yb1 = y - int(4 * scale), y + line_h - int(2 * scale)
    for ch, color, bg in cells:
        if bg is not None:
            draw.rectangle([x, yb0, x + cw, yb1], fill=bg)
        draw.text((x, y), ch, font=font, fill=color)
        x += cw
    img.save(out_path)
    return W, H, len(cells)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "out.png"
    data = sys.stdin.read()
    w, h, n = render(data, out)
    print(f"wrote {out}  {w}x{h}px  {n} cells")
