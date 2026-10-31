"""The shipped .ico must be the icon the app actually draws.

This exists because they silently diverged. scripts/draw_icon.py wrote the
1024 px master and the four Tk asset PNGs, but the Windows .ico was a manual
ImageMagick step recorded only in a comment in the .iss. So when the icon was
redrawn on 2026-10-31 the window showed the new artwork while the taskbar,
Explorer and the installer all showed the 2026-10-18 artwork -- reported from
the first Windows test as "its taskbar icon is the old version".

draw_icon.py now writes the .ico from the same master, and this checks the
committed files agree, so the next redraw cannot half-land.

Needs Pillow, which is a tool dependency and not an app one (the app ships
only onnxruntime and sounddevice). Prints SKIPPED without it.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MASTER = ROOT / "packaging/icons/music-assassin-live-1024.png"
ICO = ROOT / "packaging/icons/music-assassin-live.ico"
ASSETS = ROOT / "assassin_live/ui/assets"

# Windows picks the nearest frame and scales it: no 256 means a soft Explorer
# tile, no 16 means a muddy titlebar.
WANT_FRAMES = {(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)}

# How far a frame may differ from the master, mean absolute difference per
# channel on 0-255. Not zero: each frame is a Lanczos downsample, so the
# comparison resamples too and the two paths round differently. The failure
# this catches is different artwork, which measured 142 when it happened.
TOLERANCE = 6.0


def _mean_diff(a, b) -> float:
    from PIL import ImageChops
    px = list(ImageChops.difference(a, b).convert("L").getdata())
    return sum(px) / len(px)


def test_ico_has_the_frames_windows_asks_for():
    from PIL import Image
    with Image.open(ICO) as ico:
        frames = set(ico.ico.sizes())
    missing = WANT_FRAMES - frames
    assert not missing, f"{ICO.name} is missing frames: {sorted(missing)}"
    print(f"  ico frames     -> all {len(WANT_FRAMES)} present")


def test_every_ico_frame_is_the_current_artwork():
    from PIL import Image
    with Image.open(MASTER) as m:
        master = m.convert("RGBA")
    diffs = []
    for size in sorted(WANT_FRAMES, reverse=True):
        with Image.open(ICO) as ico:
            ico.size = size
            frame = ico.convert("RGBA")
        want = master.resize(size, Image.LANCZOS)
        d = _mean_diff(frame, want)
        diffs.append((d, size))
        assert d <= TOLERANCE, (
            f"{ICO.name} frame {size[0]}px differs from the master by "
            f"{d:.1f} (limit {TOLERANCE}). Regenerate it: "
            "python scripts/draw_icon.py")
    worst_d, worst_size = max(diffs)
    print(f"  ico artwork    -> matches master (worst {worst_d:.2f} "
          f"at {worst_size[0]}px)")


def test_tk_assets_are_the_current_artwork_too():
    from PIL import Image
    with Image.open(MASTER) as m:
        master = m.convert("RGBA")
    for name, px in (("icon.png", 128), ("icon_32.png", 32),
                     ("icon_48.png", 48), ("icon_64.png", 64)):
        path = ASSETS / name
        assert path.is_file(), f"missing {path}"
        with Image.open(path) as im:
            got = im.convert("RGBA")
        assert got.size == (px, px), f"{name} is {got.size}, expected {px}px"
        d = _mean_diff(got, master.resize((px, px), Image.LANCZOS))
        assert d <= TOLERANCE, (
            f"{name} differs from the master by {d:.1f} "
            f"(limit {TOLERANCE}). Regenerate: python scripts/draw_icon.py")
    print("  tk assets      -> 4 sizes, all current")


def main():
    print("\nicon assets")
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("  SKIPPED (needs Pillow)")
        print("PASS")
        return 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
