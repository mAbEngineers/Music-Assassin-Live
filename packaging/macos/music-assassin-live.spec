# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the macOS .app.

Used by scripts/build_macos.sh. The script stages the model files into
Contents/Resources/models after PyInstaller builds the app, then signs the
whole bundle; that is why codesign_identity is left None here.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parents[1]
VERSION = os.environ.get("MAL_VERSION", "0.0.0")
ICON = os.environ.get("MAL_ICON") or None

datas = []
binaries = []
hiddenimports = ["tkinter"]

for package in ("onnxruntime", "sounddevice", "_sounddevice_data"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# The UI loads these at runtime through _icon_path(), so they must be beside
# the executable in the onefile-independent app layout.
assets = ROOT / "assassin_live" / "ui" / "assets"
for name in ("icon.png", "icon_32.png", "icon_48.png", "icon_64.png"):
    path = assets / name
    if path.is_file():
        datas.append((str(path), "."))

analysis = Analysis(
    [str(ROOT / "scripts" / "run_assassin_live.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="music-assassin-live",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="music-assassin-live",
)

app = BUNDLE(
    coll,
    name="Music Assassin Live.app",
    icon=ICON,
    bundle_identifier="com.mabengineers.music-assassin-live",
    info_plist={
        "CFBundleDisplayName": "Music Assassin Live",
        "CFBundleName": "Music Assassin Live",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "14.4",
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHighResolutionCapable": True,
        "NSAudioCaptureUsageDescription":
            "Music Assassin Live captures system audio so it can remove "
            "background music from speech and video.",
    },
)
