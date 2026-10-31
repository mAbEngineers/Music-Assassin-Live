#!/usr/bin/env python3
"""A crash has to leave a trace, and has to put the audio back.

Both halves of diagnostics.py matter for the same reason: the Windows build
is --windowed, so it has no console. Before this existed, "it crashes after
a while" came with no traceback anywhere on the machine, and a crash while
the filter was on left the virtual cable holding the system default -- a
silent laptop, with nothing on screen and nothing in Sound settings looking
obviously wrong.

Hardware-free: the restore hook here is a counter, not a real backend.
What is being tested is that the hooks fire at all, on the thread where the
failure actually happens, and that nothing in this path can raise.
"""

import faulthandler
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tempfile                                     # noqa: E402

from assassin_live import diagnostics               # noqa: E402


class Restore:
    """Stands in for App._emergency_restore."""

    def __init__(self, raises=False):
        self.calls = 0
        self.raises = raises

    def __call__(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("the restore itself is broken")


def _isolated(fn):
    """Run fn(restore) with CRASH_LOG in a temp dir and the global hooks put
    back afterwards.

    install() reaches into sys and threading, so a test that forgot to
    restore them would quietly rewire the rest of the suite.
    """
    saved = (diagnostics.CRASH_LOG, sys.excepthook, threading.excepthook,
             diagnostics._restore_hook)
    with tempfile.TemporaryDirectory() as d:
        diagnostics.CRASH_LOG = Path(d) / "crash.log"
        try:
            restore = Restore()
            diagnostics.set_restore_hook(restore)
            return fn(restore)
        finally:
            (diagnostics.CRASH_LOG, sys.excepthook, threading.excepthook,
             diagnostics._restore_hook) = saved
            # install() pointed faulthandler at a file in the temp dir that
            # is about to be deleted, and it holds the fd, not the path.
            faulthandler.disable()
            diagnostics._fault_file = None


def test_a_worker_thread_crash_is_recorded_and_restores():
    """The case that actually happened: the engine and the enable/disable
    workers are spawned threads, and a thread that dies takes its traceback
    with it -- the window stays up looking perfectly healthy."""
    def go(restore):
        diagnostics.install()

        def boom():
            raise ValueError("stem separation fell over")

        t = threading.Thread(target=boom, name="engine")
        t.start()
        t.join(5)
        assert not t.is_alive()

        log = diagnostics.CRASH_LOG.read_text()
        assert "stem separation fell over" in log, f"not logged:\n{log}"
        assert "ValueError" in log
        assert "thread: engine" in log, "did not say which thread died"
        assert restore.calls == 1, f"restore hook ran {restore.calls} times"
        print("  worker thread  -> traceback logged, routing restored")
    _isolated(go)


def test_the_log_names_the_build_it_came_from():
    """A crash report is worth having only if it says what crashed."""
    def go(restore):
        diagnostics.record("main", ValueError, ValueError("x"), None)
        log = diagnostics.CRASH_LOG.read_text()
        from assassin_live import __version__
        for want in (__version__, sys.platform, "music-assassin-live"):
            assert want in log, f"log does not mention {want!r}:\n{log}"
        print(f"  header         -> version, platform, thread ({__version__})")
    _isolated(go)


def test_a_tk_callback_crash_is_caught():
    """Tk routes callback exceptions through the widget, not excepthook, and
    its own handler prints to the stderr this build does not have and then
    carries on -- so a tick that raises every second is invisible. Most of
    this app's work runs in an after() tick."""
    class FakeRoot:
        report_callback_exception = None

    def go(restore):
        root = FakeRoot()
        diagnostics.attach_to_tk(root)
        assert callable(root.report_callback_exception), "nothing attached"
        try:
            raise KeyError("missing endpoint")
        except KeyError:
            root.report_callback_exception(*sys.exc_info())
        assert "missing endpoint" in diagnostics.CRASH_LOG.read_text()
        assert restore.calls == 1
        print("  tk callback    -> caught, logged, routing restored")
    _isolated(go)


def test_a_broken_restore_hook_does_not_replace_the_report():
    """The hook touches COM on a process that is already failing. If it
    throws, the traceback we were writing must still survive -- losing the
    report to a secondary failure is how the original crash stayed
    unexplained for a week."""
    def go(restore):
        diagnostics.set_restore_hook(Restore(raises=True))
        diagnostics.install()

        t = threading.Thread(target=lambda: 1 / 0, name="worker")
        t.start()
        t.join(5)
        assert "ZeroDivisionError" in diagnostics.CRASH_LOG.read_text()
        print("  broken hook    -> swallowed, report kept")
    _isolated(go)


def test_record_survives_an_unwritable_log():
    """Diagnostics are never a precondition for anything."""
    def go(restore):
        diagnostics.CRASH_LOG = Path("/nonexistent-dir-xyz/crash.log")
        diagnostics.record("main", ValueError, ValueError("x"), None)
        print("  unwritable     -> returns quietly instead of raising")
    _isolated(go)


def test_the_log_is_bounded():
    """An app crashing in a loop must not fill the disk, and it is the most
    recent crash anyone reads."""
    def go(restore):
        diagnostics.CRASH_LOG.write_bytes(b"old\n" * 200_000)
        diagnostics.record("main", ValueError, ValueError("newest"), None)
        size = diagnostics.CRASH_LOG.stat().st_size
        assert size < 256 * 1024, f"grew to {size} bytes"
        log = diagnostics.CRASH_LOG.read_text(errors="replace")
        assert "newest" in log, "trimmed away the entry it was adding"
        assert "trimmed" in log, "trimmed silently, with no marker"
        print(f"  bounded        -> trimmed to {size // 1024} KiB, tail kept")
    _isolated(go)


def main():
    print("\ncrash reporting")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
