#!/usr/bin/env python3
"""The startup guard, and the crash it replaces.

Hardware-free and PipeWire-free by construction -- it works by taking
PipeWire *away*, so it is as meaningful on a machine that has it as on one
that does not.

The bug being pinned: backends/pipewire.py's _pw_dump() caught
subprocess.SubprocessError, but a missing binary raises FileNotFoundError,
which is an OSError and not a SubprocessError. RoutingSession.recover_stale()
runs at startup from both __main__ and ui.app, so on any machine without
pw-dump -- every Windows machine among them -- it raised before the window
opened. A packaged Windows build would have shown a crash dialog and nothing
else.
"""

import os
import shutil
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio import routing                      # noqa: E402
from assassin_live.audio.backends import macos               # noqa: E402
from assassin_live.audio.backends import pipewire            # noqa: E402
from assassin_live.audio.backends import windows             # noqa: E402
from assassin_live import paths                              # noqa: E402


def test_premise():
    """If this ever fails, the bug this file exists for cannot happen and
    the rest of these tests are measuring nothing."""
    assert not isinstance(FileNotFoundError(), subprocess.SubprocessError), \
        "FileNotFoundError IS a SubprocessError -- premise of this file is wrong"
    print("  premise        -> FileNotFoundError is not a SubprocessError")


def test_backend_table_covers_the_three_desktop_platforms():
    assert routing._backend_for("darwin") is macos.MacOSBackend
    assert routing._backend_for("sunos5") is None
    assert routing._backend_for("linux") is pipewire.PipeWireBackend
    assert routing._backend_for("win32") is windows.WindowsBackend
    print("  platform table -> linux/win32/darwin map, nothing else does")


def test_reason_names_the_platform_when_no_backend():
    """sunos5 rather than darwin: all three desktop platforms have a backend
    now, so asking about one of them no longer exercises the no-backend path."""
    real = routing.sys.platform
    try:
        routing.sys.platform = "sunos5"
        reason = routing.unsupported_reason()
        assert reason, "no reason given on a platform with no backend"
        assert "sunos5" in reason, f"reason does not name the platform: {reason!r}"
        # It has to be actionable, not just a refusal.
        for name in ("PipeWire", "Core Audio", "virtual audio cable"):
            assert name in reason, f"reason does not mention {name}: {reason!r}"
        print("  no backend     -> refused, and says why")
    finally:
        routing.sys.platform = real


def test_windows_backend_refuses_without_its_prerequisites():
    """The guard has to hold on both platforms, and it refuses for a
    different reason on each -- which is the whole point of
    unsupported_reason() living on the backend rather than in one shared
    check. Asserting the wording, not just that something was returned,
    because a guard that fires for the wrong reason tells the user to fix
    the wrong thing.
    """
    reason = windows.WindowsBackend.unsupported_reason()
    if sys.platform.startswith("win"):
        # The platform is fine here; what may be missing is a virtual cable.
        # A CI runner has none. A developer box might.
        if reason is None:
            print("  windows        -> a cable is present, backend usable")
        else:
            assert "cable" in reason.lower(), \
                f"refused on Windows for an unexpected reason: {reason!r}"
            print("  windows no cable-> refused, and names the cable")
    else:
        # routing.py imports this module unconditionally, so it has to refuse
        # here rather than reach for COM.
        assert reason, f"the Windows backend did not refuse on {sys.platform}"
        assert sys.platform in reason, \
            f"reason does not name the platform: {reason!r}"
        print("  windows off-win -> refused, and names the platform")


def test_macos_backend_refuses_without_its_prerequisites():
    """The same guard for the third backend. On a Mac it should be usable
    (or refuse for a version/architecture reason); elsewhere it must refuse
    by naming the platform rather than touching Core Audio."""
    reason = macos.MacOSBackend.unsupported_reason()
    if sys.platform.startswith("darwin"):
        if reason is None:
            print("  macOS          -> a supported Mac, backend usable")
        else:
            assert ("macOS" in reason or "Apple Silicon" in reason
                    or "Core Audio" in reason), \
                f"refused on macOS for an unexpected reason: {reason!r}"
            print("  macOS          -> refused, and names the requirement")
    else:
        assert reason, f"the macOS backend did not refuse on {sys.platform}"
        assert sys.platform in reason, \
            f"reason does not name the platform: {reason!r}"
        print("  macOS off-mac  -> refused, and names the platform")


def test_reason_names_every_missing_tool():
    real_platform, real_which = sys.platform, shutil.which

    def fake_which(cmd, *a, **kw):
        return None if cmd in pipewire.REQUIRED_TOOLS else real_which(cmd, *a, **kw)

    try:
        pipewire.sys.platform = "linux"
        pipewire.shutil.which = fake_which
        reason = pipewire.PipeWireBackend.unsupported_reason()
        assert reason, "no reason given when the PipeWire tools are absent"
        for tool in pipewire.REQUIRED_TOOLS:
            assert tool in reason, f"{tool} missing from: {reason!r}"
        print("  tools missing  -> refused, and names every one of them")
    finally:
        pipewire.sys.platform = real_platform
        pipewire.shutil.which = real_which


def test_frozen_build_finds_models_beside_the_exe():
    """The other way a packaged Windows build fails before it does anything.

    The installer lays models out in {app}\\models, beside the .exe. The
    obvious guess -- the repo-relative fallback in models_dir() -- is wrong
    for a PyInstaller onefile build, where __file__ points inside the
    temporary extraction directory, so it would look in _MEIPASS and find
    nothing. The .deb never hit this because it installs to an absolute
    SYSTEM_MODELS_DIR, which is why no packaged build had caught it.
    """
    assert paths.bundled_models_dir() is None, \
        "claimed a bundled model dir while running from source"
    real_frozen = getattr(sys, "frozen", None)
    real_exe = sys.executable
    try:
        sys.frozen = True
        sys.executable = os.path.join("C:\\", "Program Files", "MAL", "mal.exe")
        got = paths.bundled_models_dir()
        assert got is not None, "no bundled model dir in a frozen build"
        assert got.name == "models", got
        assert got.parent.name == "MAL", \
            f"looked beside {got.parent}, not beside the executable"
        print("  frozen build   -> looks for models beside the .exe")
    finally:
        sys.executable = real_exe
        if real_frozen is None:
            del sys.frozen
        else:
            sys.frozen = real_frozen


def _without_subprocess(fn):
    """Run fn() with every subprocess.run raising FileNotFoundError, as it
    does when the binary is not installed."""
    real_run = subprocess.run

    def fake_run(cmd, *a, **kw):
        raise FileNotFoundError(2, "No such file or directory", cmd[0])

    pipewire.subprocess.run = fake_run
    try:
        return fn()
    finally:
        pipewire.subprocess.run = real_run


def test_pw_dump_survives_a_missing_binary():
    out = _without_subprocess(lambda: pipewire._pw_dump("Sink"))
    assert out == [], f"expected [], got {out!r}"
    print("  missing pw-dump-> [] rather than FileNotFoundError")


def test_recover_stale_does_not_raise():
    """The exact startup path that used to crash: __main__ and ui.app both
    call this before anything else happens."""
    _without_subprocess(routing.RoutingSession.recover_stale)
    print("  recover_stale  -> returns instead of crashing startup")


def main():
    print("\nplatform guard")
    test_premise()
    test_backend_table_covers_the_three_desktop_platforms()
    test_reason_names_the_platform_when_no_backend()
    test_windows_backend_refuses_without_its_prerequisites()
    test_macos_backend_refuses_without_its_prerequisites()
    test_frozen_build_finds_models_beside_the_exe()
    test_reason_names_every_missing_tool()
    test_pw_dump_survives_a_missing_binary()
    test_recover_stale_does_not_raise()
    print("PASS")


if __name__ == "__main__":
    main()
