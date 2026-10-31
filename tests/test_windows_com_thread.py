"""Core Audio must work from a thread we spawned, not just the main one.

WHY THIS FILE EXISTS, and why it is separate from test_windows_routing.py:
that suite replaces CoreAudio with a fake, which is what makes the state
machine testable on Linux -- and is exactly why it could not catch this.

The first run of the Windows build on real hardware failed with "No hardware
audio sink found (Bluetooth asleep?)" on a laptop whose own speakers were
working. The cause was not audio at all. comtypes calls CoInitialize() for
the thread that imports it and no other, and ui/app.py runs enable() on a
worker thread so the Tk loop keeps painting. So:

  * startup, main thread:  unsupported_reason() enumerated fine, found the
                           cable, let the app open;
  * the toggle, worker thread: every COM call raised "CoInitialize has not
                           been called", render_endpoints() swallowed it and
                           returned [], and the backend correctly concluded
                           there was nothing to play to.

CoreAudio swallows exceptions by design -- a backend that throws while
putting the user's device back is worse than one that reports it could not --
so a test that only checks the return value sees [] on a CI runner either
way. This one calls the COM path directly, where the exception is still
visible, and runs it on a thread that has never touched comtypes.

Windows-only; prints SKIPPED anywhere else.
"""

import sys
import threading


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


def test_bare_thread_is_the_hazard():
    """Informational, deliberately not an assertion.

    A fresh thread with no CoInitialize is expected to fail, which is what
    makes the next test meaningful. If this ever starts succeeding -- a
    comtypes that initialises per thread by itself -- the guard below has
    become redundant rather than broken, so this must not fail the build.
    """
    ok, detail = _on_fresh_thread(_probe)
    if ok:
        print(f"  bare thread    -> worked ({detail}); _ensure_com now redundant")
    else:
        print(f"  bare thread    -> fails as expected ({detail})")


def test_ensure_com_makes_a_worker_thread_usable():
    from assassin_live.audio.backends.windows import _ensure_com

    def probe():
        _ensure_com()
        return _probe()

    ok, detail = _on_fresh_thread(probe)
    assert ok, (
        "COM is still unusable on a worker thread after _ensure_com(): "
        f"{detail}. ui/app.py calls enable() on such a thread, so this is "
        "the 'no hardware audio sink found' bug returning.")
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
