"""Platform data paths: models, settings, state and crash logs.

Linux and Windows keep the existing XDG-style layout. macOS uses the
platform's own conventions instead: Application Support for data/config/state
and Logs for the crash file. Explicit XDG_* environment variables still win,
so a developer or CI job can redirect everything without special cases.
"""

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

    A macOS .app puts them in Contents/Resources/models. The executable lives
    in Contents/MacOS, so the resource path is two parents up. The directory
    is checked before being returned: tests and a non-bundled frozen build
    still expect the Windows {app}/models fallback.

    The .deb does not need this -- it installs to SYSTEM_MODELS_DIR, which is
    an absolute path -- which is exactly why the gap went unnoticed: every
    packaged build so far has been a .deb.
    """
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        resources = exe.parent.parent / "Resources" / "models"
        if resources.is_dir():
            return resources
    return exe.parent / "models"


def _home_base(env_name: str, mac_default: Path, other_default: Path) -> Path:
    """An explicit XDG override, else the platform's conventional base."""
    if env_name in os.environ:
        return Path(os.environ[env_name])
    if sys.platform == "darwin":
        return mac_default
    return other_default


def data_dir() -> Path:
    base = _home_base("XDG_DATA_HOME",
                      Path.home() / "Library" / "Application Support",
                      Path.home() / ".local" / "share")
    return base / APP


def state_dir() -> Path:
    base = _home_base("XDG_STATE_HOME",
                      Path.home() / "Library" / "Application Support",
                      Path.home() / ".local" / "state")
    if sys.platform == "darwin" and "XDG_STATE_HOME" not in os.environ:
        d = base / APP / "state"
    else:
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
    base = _home_base("XDG_CONFIG_HOME",
                      Path.home() / "Library" / "Application Support",
                      Path.home() / ".config")
    d = base / APP
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    """Where crash reports go. macOS keeps logs with logs, not with state."""
    if sys.platform == "darwin" and "XDG_STATE_HOME" not in os.environ:
        d = Path.home() / "Library" / "Logs" / APP
    else:
        d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


ROUTING_STATE = state_dir() / "routing.json"
SETTINGS_FILE = config_dir() / "settings.json"
