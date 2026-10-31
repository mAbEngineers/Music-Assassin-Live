"""Core Audio must work from a thread we spawned, not just the main one.

WHY THIS FILE EXISTS, and why it is separate from test_windows_routing.py:
that suite replaces CoreAudio with a fake, which is what makes the state
machine testable on Linux -- and is exactly why it could not catch this.

The first run of the Windows build on real hardware failed with "No hardware
audio sink found (Bluetooth asleep?)" on a laptop whose own speakers were
working. One candidate explanation was threading: comtypes initialises COM
for the thread that imports it and no other, and ui/app.py runs enable() on
a worker thread so the Tk loop keeps painting, which would mean

  * startup, main thread:  unsupported_reason() enumerates, finds the cable,
                           the app opens;
  * the toggle, worker thread: COM calls fail, render_endpoints() swallows it
                           and returns [], and the backend correctly
                           concludes there is nothing to play to.

HOW MUCH OF THAT IS ESTABLISHED: the thread layout is real and the swallow is
real. Whether a bare thread actually fails is NOT -- on the GitHub runner it
succeeded (see test_bare_thread_is_the_hazard), because that depends on the
process apartment. So _ensure_com() is here as correctness, not as a
diagnosed fix, and the user's fault may yet be one of the other three ways
enable() returns None. last_enable_error exists to settle that.

What this file is still worth: CoreAudio swallows exceptions by design -- a
backend that throws while putting the user's device back is worse than one
that reports it could not -- so a test that only checks return values sees []
on a device-less runner whether COM worked or not. This calls the COM path
directly, where the exception is still visible, from a thread that has never
touched comtypes.

Windows-only; prints SKIPPED anywhere else.
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _apartment_note() -> str:
    """What apartment the process is in, which decides whether a bare thread
    can use COM at all.

    comtypes picks this at import from sys.coinit_flags: apartment-threaded
    (STA, its default) leaves a thread we spawn with no apartment, so
    CoCreateInstance there fails with CO_E_NOTINITIALIZED. Multi-threaded
    (MTA) is different -- since Windows 8 a thread that never initialised COM
    is folded into the process MTA implicitly and simply works.

    Printed rather than asserted: this is the fact that decides whether
    _ensure_com is load-bearing on a given machine, and a CI runner and a
    user's laptop can honestly differ.
    """
    try:
        import comtypes
        flags = getattr(sys, "coinit_flags", "unset")
        return f"comtypes {comtypes.__version__}, sys.coinit_flags={flags}"
    except Exception as e:                                  # noqa: BLE001
        return f"comtypes unavailable: {e}"


def _probe():
    """Raw COM enumeration, no swallow. Returns (ok, detail)."""
    try:
        from pycaw.pycaw import AudioUtilities
        enumerator = AudioUtilities.GetDeviceEnumerator()
        collection = enumerator.EnumAudioEndpoints(0, 1)
        return True, f"{collection.GetCount()} endpoint(s)"
    except Exception as e:                                  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _on_fresh_thread(fn):
    """Run fn on a thread that has never initialised COM, return its result."""
    out = {}
    t = threading.Thread(target=lambda: out.update(r=fn()), daemon=True)
    t.start()
    t.join(30)
    assert not t.is_alive(), "the probe thread hung"
    return out.get("r")


def test_report_the_apartment():
    print(f"  environment    -> {_apartment_note()}")


def test_bare_thread_is_the_hazard():
    """Informational, deliberately not an assertion.

    Whether a bare thread can use COM depends on the process apartment (see
    _apartment_note), so this legitimately differs between machines and must
    not fail a build either way. It is printed because it says whether
    _ensure_com was load-bearing *here*.

    On the GitHub runner, 2026-10-31: it worked. So this check does not
    reproduce the fault reported from the user's laptop, and _ensure_com
    stands as correctness rather than as a proven fix for that report. What
    identifies the real cause there is WindowsBackend.last_enable_error,
    which now distinguishes the four ways enable() can fail.
    """
    ok, detail = _on_fresh_thread(_probe)
    if ok:
        print(f"  bare thread    -> worked ({detail}); COM is forgiving here")
    else:
        print(f"  bare thread    -> fails without init ({detail})")


def test_ensure_com_makes_a_worker_thread_usable():
    from assassin_live.audio.backends.windows import _ensure_com

    def probe():
        _ensure_com()
        return _probe()

    ok, detail = _on_fresh_thread(probe)
    assert ok, (
        "COM is unusable on a worker thread even after _ensure_com(): "
        f"{detail}. ui/app.py calls enable() on such a thread, so whatever "
        "_ensure_com is doing, it is not making that thread work.")
    print(f"  _ensure_com    -> worker thread enumerates ({detail})")


def test_the_backend_itself_agrees_across_threads():
    """What CoreAudio reports must not depend on which thread asked.

    On a CI runner both answers are 0 endpoints, so this cannot catch the
    original bug on its own -- it is here for the machine that has audio,
    where a mismatch is the symptom the user actually saw.
    """
    from assassin_live.audio.backends.windows import CoreAudio

    core = CoreAudio()
    main = core.render_endpoints()
    worker = _on_fresh_thread(core.render_endpoints)
    assert worker is not None, "the worker thread returned nothing at all"
    assert len(main) == len(worker), (
        f"main thread saw {len(main)} endpoint(s), worker saw {len(worker)} "
        "-- the same COM calls are giving different answers per thread")
    print(f"  cross-thread   -> both threads agree ({len(main)} endpoint(s))")


def main():
    print("\nwindows COM threading")
    if not sys.platform.startswith("win"):
        print("  SKIPPED (needs Windows)")
        print("PASS")
        return 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
