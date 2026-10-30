#!/usr/bin/env python3
"""Draws the app icon: the offline app's icon (cream plate, treble clef cut by
one red blade) plus two sound waves coming off the clef's lower bowl, the part
the blade keeps. The inner wave is the clef's navy, the outer one the blade's
red: the cut is made and what is left keeps playing, live.

Each wave is a crescent, widest in the middle, turned to face straight out
from the centre of the bowl. They are bold on purpose, so both still read as
separate strokes at 32 px.

The base is a copy of the offline app's 1024 px icon, kept in
packaging/icons/offline-base-1024.png so this script needs nothing outside
the repo. The waves are drawn at 4x and downsampled for clean edges.

Writes packaging/icons/music-assassin-live-1024.png and the four PNGs the Tk
window uses (assassin_live/ui/assets/icon*.png).

Usage: python scripts/draw_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1024
SS = 4  # supersampling factor

NAVY = (29, 33, 65)          # sampled from the offline icon's clef
RED = (194, 37, 51)          # sampled from the offline icon's blade
BOWL = (527, 606)            # centre of a circle fitted to the bowl's outer edge
# (radius, x of the wave's midpoint at y = WAVE_Y, colour)
WAVES = ((92, 692, NAVY), (142, 750, RED))
WAVE_Y = 640
HALF_SPAN = 48               # degrees either side of the wave's midpoint
WIDTH = 38                   # at the middle of each wave
TAPER = 0.4                  # lower keeps the ends fuller


def _wave(d: ImageDraw.ImageDraw, radius: float, mid_x: float, colour) -> None:
    """Discs stamped along an arc whose midpoint is (mid_x, WAVE_Y) and which
    faces straight out from BOWL."""
    ang = math.atan2(WAVE_Y - BOWL[1], mid_x - BOWL[0])
    cx = mid_x - radius * math.cos(ang)
    cy = WAVE_Y - radius * math.sin(ang)
    a0 = math.degrees(ang) - HALF_SPAN
    n = int(2 * HALF_SPAN * radius / 60 * 8) + 40
    for k in range(n + 1):
        a = math.radians(a0 + 2 * HALF_SPAN * k / n)
        r = max(WIDTH * math.sin(math.pi * k / n) ** TAPER / 2, 0.8)
        x = cx + radius * math.cos(a)
        y = cy + radius * math.sin(a)
        d.ellipse(((x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS),
                  fill=colour + (255,))


def draw() -> Image.Image:
    base = Image.open(ROOT / "packaging/icons/offline-base-1024.png").convert("RGBA")
    mark = Image.new("RGBA", (SIZE * SS, SIZE * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(mark)
    for radius, mid_x, colour in WAVES:
        _wave(d, radius, mid_x, colour)
    return Image.alpha_composite(base, mark.resize((SIZE, SIZE), Image.LANCZOS))


def main() -> None:
    icon = draw()
    icon.save(ROOT / "packaging/icons/music-assassin-live-1024.png")
    assets = ROOT / "assassin_live/ui/assets"
    for name, px in (("icon.png", 128), ("icon_32.png", 32),
                     ("icon_48.png", 48), ("icon_64.png", 64)):
        icon.resize((px, px), Image.LANCZOS).save(assets / name)
    print("wrote 1024 + 4 asset sizes")


if __name__ == "__main__":
    main()
