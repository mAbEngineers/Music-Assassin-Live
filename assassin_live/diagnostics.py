"""Somewhere for a crash to go when there is no console to print it to.

The Windows build is PyInstaller --windowed, which means the process has no
console and no valid stderr: every traceback it produces is written to a
handle nobody owns and lost. The first long run on real hardware ended with
"it crashes after a while" and not one byte of evidence anywhere on the
machine, which is a reporting failure as much as a crash.

Three kinds of death, and they need three different catchers:

  * an exception on the main thread            -> sys.excepthook
  * an exception on a thread we spawned        -> threading.excepthook
    (the engine's and the enable/disable workers; without this the thread
    dies quietly and the app stays up looking fine but doing nothing)
  * a fault in native code                     -> faulthandler
    (onnxruntime and PortAudio are C; an access violation there never
    reaches Python at all, so no hook can see it -- faulthandler writes the
    C-level stack from a signal handler before the process goes down)

The restore hook is called for the two Python cases, after the log is
written, and is what puts the user's speakers back. It cannot run for the
native case: nothing Python-level runs after an access violation, which is
why RoutingSession.recover_stale() also runs at startup.

install() is separate from set_restore_hook() because the hooks want to be
in place before anything can fail, and the thing that knows how to restore
the audio does not exist that early.
"""

import faulthandler
import sys
import threading
import time
import traceback

from . import __version__
from .paths import log_dir

CRASH_LOG = log_dir() / "crash.log"

# Keep the tail, drop the head, when the file gets big. An app that crashes
# in a loop would otherwise fill the disk, and it is the most recent crash
# that anyone reads.
_MAX_BYTES = 256 * 1024


def _trim() -> None:
    try:
        if CRASH_LOG.stat().st_size <= _MAX_BYTES:
            return
        tail = CRASH_LOG.read_bytes()[-(_MAX_BYTES // 2):]
        CRASH_LOG.write_bytes(b"[earlier entries trimmed]\n" + tail)
    except OSError:
        pass


def record(where: str, exc_type, exc, tb) -> None:
    """Append one traceback to the crash log. Never raises.

    Never raises is load-bearing: this runs from inside an exception handler,
    and an exception here would replace the report with nothing.
    """
    try:
        _trim()
        with CRASH_LOG.open("a", encoding="utf-8", errors="replace") as f:
            f.write(f"\n{'=' * 70}\n"
                    f"{time.strftime('%Y-%m-%d %H:%M:%S')}  "
                    f"music-assassin-live {__version__}  "
                    f"{sys.platform}  python {sys.version.split()[0]}\n"
                    f"thread: {where}\n\n")
            traceback.print_exception(exc_type, exc, tb, file=f)
            f.flush()
    except Exception:       # noqa: BLE001
        pass


_restore_hook = None
# faulthandler writes to a file descriptor, not to a Python object, and does
# not own the object it was handed. Drop the only reference and the file is
# garbage-collected, the fd closes, and the fault report goes nowhere -- so
# this global is the reference, held deliberately for the life of the
# process and never closed.
_fault_file = None


def set_restore_hook(fn) -> None:
    """What to run after a logged crash, to undo the audio routing."""
    global _restore_hook
    _restore_hook = fn


def install() -> None:
    """Point all three catchers at the crash log. Call once, early.

    Early because faulthandler has to hold the log file open before the
    fault: it writes from a signal handler, where opening a file is not
    something it can safely do.
    """
    global _fault_file
    try:
        # Opened here, before the fault, because faulthandler writes from a
        # signal handler -- where opening a file is not something it can
        # safely do. Kept in a global for the reason described there.
        _fault_file = CRASH_LOG.open("a", encoding="utf-8", errors="replace")
        faulthandler.enable(file=_fault_file)
    except Exception:       # noqa: BLE001
        pass                # a crash log is diagnostics, never a precondition

    def _main_hook(exc_type, exc, tb):
        record("main", exc_type, exc, tb)
        _run()
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        record(getattr(args.thread, "name", "?"),
               args.exc_type, args.exc_value, args.exc_traceback)
        _run()

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


def attach_to_tk(root) -> None:
    """Catch exceptions raised inside Tk callbacks.

    Separate from install() because Tk routes these through the widget
    rather than sys.excepthook, and its own handler prints to the stderr
    that does not exist here and then carries on -- so a tick that raises
    every second leaves a window that looks alive and does nothing. Most of
    this app's work happens in an after() tick, which makes this the hook
    most likely to fire.
    """
    def _handler(exc_type, exc, tb):
        record("tk callback", exc_type, exc, tb)
        _run()
    try:
        root.report_callback_exception = _handler
    except Exception:       # noqa: BLE001
        pass


def _run() -> None:
    if _restore_hook is None:
        return
    try:
        _restore_hook()
    except Exception:       # noqa: BLE001
        pass                # already crashing; a second one adds nothing
