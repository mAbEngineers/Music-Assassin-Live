"""XDG-style paths: models in data dir, crash-recovery state in state dir."""

import os
import sys
from pathlib import Path

APP = "music-assassin"

# Populated by scripts/build_deb.sh with the subset of models whose upstream
# license permits redistribution (see models/README.md) — read-only, shared
# across all users on the machine, so a .deb install works with zero
# post-install steps for those models.
SYSTEM_MODELS_DIR = Path("/usr/share/music-assassin-live/models")


def bundled_models_dir() -> "Path | None":
    """Models shipped beside a packaged executable, or None when not frozen.

    The Windows installer puts them in {app}/models, next to the .exe. That
    needs saying explicitly because the obvious guess is wrong: in a
    PyInstaller onefile build __file__ points inside the temporary extraction
    directory, so the repo-relative fallback below resolves to _MEIPASS and
    never sees them. sys.executable is the installed .exe itself.

    The .deb does not need this -- it installs to SYSTEM_MODELS_DIR, which is
    an absolute path -- which is exactly why the gap went unnoticed: every
    packaged build so far has been a .deb.
    """
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent / "models"


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    return base / APP


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    d = base / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    env = os.environ.get("MUSIC_ASSASSIN_MODELS")
    if env:
        return Path(env)
    xdg = data_dir() / "models"
    if xdg.is_dir() and any(xdg.glob("*.onnx")):
        return xdg
    # dev fallback: repo-local models/ next to the package
    local = Path(__file__).resolve().parent.parent / "models"
    if local.is_dir() and any(local.glob("*.onnx")):
        return local
    # Checked after the user's own directory, matching how the .deb's
    # SYSTEM_MODELS_DIR is treated: models the user supplied themselves win
    # over the ones we shipped.
    bundled = bundled_models_dir()
    if bundled is not None and bundled.is_dir() and any(bundled.glob("*.onnx")):
        return bundled
    if SYSTEM_MODELS_DIR.is_dir() and any(SYSTEM_MODELS_DIR.glob("*.onnx")):
        return SYSTEM_MODELS_DIR
    xdg.mkdir(parents=True, exist_ok=True)
    return xdg


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


ROUTING_STATE = state_dir() / "routing.json"
SETTINGS_FILE = config_dir() / "settings.json"
