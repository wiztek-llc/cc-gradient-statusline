#!/usr/bin/env python3
"""Stack several demo status lines into one PNG to show the full gradient range."""
import subprocess
from PIL import Image
import render_png as R

LEVELS = [
    (8, 5), (22, 14), (38, 30), (55, 47),
    (72, 61), (86, 78), (95, 90), (99, 97),
]

imgs = []
for p5, p7 in LEVELS:
    ansi = subprocess.run(
        ["python3", "statusline.py", "--demo", "--pct5", str(p5), "--pct7", str(p7)],
        capture_output=True, text=True,
    ).stdout
    tmp = f"/tmp/_sc_{p5}.png"
    R.render(ansi, tmp)
    imgs.append(Image.open(tmp))

W = max(i.width for i in imgs)
gap = 6
H = sum(i.height for i in imgs) + gap * (len(imgs) - 1)
canvas = Image.new("RGB", (W, H), R.BG)
y = 0
for im in imgs:
    canvas.paste(im, (0, y))
    y += im.height + gap
canvas.save("/tmp/showcase.png")
print(f"wrote /tmp/showcase.png {W}x{H}")
