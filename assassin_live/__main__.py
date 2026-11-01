"""python -m assassin_live [--headless --model NAME] [--recover] [--list-models]"""

import argparse
import os
import sys
import time

# Match the GUI's cadence: the capture check costs a pw-dump and the states
# it catches are structural, so once every few seconds is enough.
CAPTURE_CHECK_TICKS = 5

from .paths import models_dir
from . import processors


def _report_unsupported(reason: str, gui: bool) -> None:
    """Say why we cannot run, through a channel the user can actually see.

    stderr always, because it is scriptable and shows up in journals. Plus a
    dialog on the GUI path: a --windowed PyInstaller build has no console
    attached, so a printed message there reaches nobody, which is exactly
    how "it just doesn't open" bug reports get written.
    """
    print(reason, file=sys.stderr)
    if not gui:
        return
    try:
        import tkinter
        from tkinter import messagebox

        from .ui.app import enable_dpi_awareness
        enable_dpi_awareness()      # this dialog is the first window, so here too
        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("Music Assassin Live", reason)
        root.destroy()
    except Exception:
        pass    # no display or no tk: stderr above is the fallback


def _say_recovered() -> None:
    """Confirm the restore in a window, for the no-console build."""
    try:
        import tkinter
        from tkinter import messagebox

        from .ui.app import enable_dpi_awareness
        enable_dpi_awareness()
        root = tkinter.Tk()
        root.withdraw()
        if sys.platform.startswith("darwin"):
            text = (
                "macOS restores audio automatically when Music Assassin "
                "Live stops or crashes. There is no system default or virtual "
                "cable to recover on this platform.")
        else:
            text = (
                "Your normal playback device has been restored.\n\n"
                "If sound is still going to the virtual cable, open Sound "
                "settings and choose your speakers or headphones there.")
        messagebox.showinfo("Music Assassin Live", text)
        root.destroy()
    except Exception:       # noqa: BLE001
        pass    # no display or no tk: the printed line above is the fallback


def main():
    # Before anything can fail. On the --windowed Windows build stderr goes
    # nowhere, so without this a crash is not just unreported, it is
    # unrecorded: the first long run on hardware died with no trace of why
    # anywhere on the machine. See diagnostics.py.
    from . import diagnostics
    diagnostics.install()

    ap = argparse.ArgumentParser(prog="assassin_live")
    ap.add_argument("--headless", action="store_true",
                    help="no GUI; enable filter until Ctrl-C")
    # macOS's primary case is speech over background music, where the speech
    # enhancer is the stronger live choice. Other platforms keep stemgen.
    ap.add_argument("--model",
                    default="dpdfnet_hr" if sys.platform == "darwin"
                    else "stemgen")
    ap.add_argument("--bypass", action="store_true",
                    help="route audio but skip processing (plumbing test)")
    ap.add_argument("--midside", action="store_true",
                    help="enable the mid/side stereo pre-filter ahead of the model")
    ap.add_argument("--stereo", action="store_true",
                    help="rebuild the stereo image around the mono model "
                         "(ROADMAP B3); off by default, same as the GUI")
    ap.add_argument("--no-bandlimit", action="store_true",
                    help="disable the ~20 Hz-20 kHz band-limit on the processed output (on by default)")
    ap.add_argument("--limit-db", type=float, default=0.0, metavar="DB",
                    help="suppression limit: how far any bin may be pulled "
                         "down, any model (0 = unlimited, the default)")
    ap.add_argument("--smoothing-ms", type=float, default=0.0, metavar="MS",
                    help="how slowly a bin's gain may fall, against vocal "
                         "loss and pumping (0 = off, the default)")
    ap.add_argument("--list-models", action="store_true")
    ap.add_argument("--recover", action="store_true",
                    help="clean up stale trap sinks and restore default sink")
    ap.add_argument("--check-cable", action="store_true",
                    help="exit 0 if a virtual audio cable endpoint exists, "
                         "1 if not, 3 if it cannot be determined; for the "
                         "Windows installer")
    args = ap.parse_args()

    if args.list_models:
        for n in processors.available(models_dir()):
            print(n)
        return

    # Before the unsupported_reason() guard below, deliberately: the guard
    # refuses to run *because* there is no cable, and this is the question
    # "is there a cable", asked by the installer mid-install. Answering it
    # with an error dialog and exit 2 would be useless to the caller.
    if args.check_cable:
        from .audio.routing import trap_render_present
        present = trap_render_present()
        if present is None:
            # Not Windows, or pycaw/comtypes missing. Distinct exit code so
            # the installer falls back to its own heuristic instead of
            # reading this as "no cable, install the driver".
            print("cannot determine")
            raise SystemExit(3)
        print("cable present" if present else "no cable")
        raise SystemExit(0 if present else 1)

    # Everything below drives a platform audio graph. Check one is actually
    # reachable before touching it, so an unsupported platform or a missing
    # PipeWire produces a sentence instead of a FileNotFoundError traceback
    # on 'pw-dump'. --list-models above needs none of it, which is why the
    # guard sits here rather than at the top of main().
    from .audio.routing import unsupported_reason
    reason = unsupported_reason()
    if reason:
        _report_unsupported(reason, gui=not args.headless and not args.recover)
        raise SystemExit(2)

    if args.recover:
        from .audio.routing import RoutingSession
        RoutingSession.recover_stale()
        print("recovered")
        if not args.headless:
            # This is the Start-menu "Restore my speakers" shortcut, clicked
            # by someone whose machine has gone quiet. A --windowed build
            # prints to nobody, so silently exiting looks like a shortcut
            # that does nothing -- the same complaint, now about the fix.
            _say_recovered()
        return

    if not args.headless:
        from .ui.app import main as gui_main
        gui_main()
        return

    from .audio.engine import AudioEngine
    from .audio.routing import RoutingSession

    routing = RoutingSession()
    routing.recover_stale()
    proc = processors.create(args.model, models_dir())
    real = routing.enable()
    if real is None:
        routing.disable()
        raise SystemExit("no hardware sink available")
    engine = AudioEngine(proc, routing)
    engine.set_bypass(args.bypass)
    engine.set_midside(args.midside)
    engine.set_bandlimit(not args.no_bandlimit)
    engine.set_stereo(args.stereo)
    engine.set_atten_limit(args.limit_db)
    engine.set_smoothing(args.smoothing_ms)
    engine.start(routing.monitor_source, real.name)
    routing.adopt_volume()
    print(f"filtering -> {real.name}  (model: {proc.name}, Ctrl-C to stop)")
    ticks = 0
    try:
        while True:
            time.sleep(1)
            if not engine.stream_ok:
                # stream died silently (uncaught callback exception or the
                # device vanishing) -- try the same recovery as a real
                # sink change before giving up.
                try:
                    engine.retarget(routing.monitor_source, routing.real.name)
                    print("\naudio stream recovered automatically")
                except Exception as e:  # noqa: BLE001
                    print(f"\naudio stream died, restart failed, stopping: {e}")
                    break
                continue
            # Keep the volume keys working (C2) — they act on the trap,
            # whose output goes nowhere, so without this they do nothing.
            try:
                routing.sync_volume()
            except Exception:  # noqa: BLE001 — a volume mirror that cannot
                pass          # run is a wart, not a reason to stop audio.

            # Same as the GUI: a stall left the capture side behind and the
            # input is quiet, so a fresh stream clears it unheard.
            if engine.needs_resync and routing.real:
                behind = engine.capture_backlog_ms
                try:
                    engine.retarget(routing.monitor_source, routing.real.name)
                    print(f"\nback in sync: dropped {behind:.0f} ms of delay")
                except Exception as e:  # noqa: BLE001
                    print(f"\nresync failed, stopping: {e}")
                    break
                continue

            event = routing.check()
            # Both device events are followed. real_sink_replaced was split
            # out from real_sink_changed for the GUI (a vanished device is
            # not a user's choice and must not overwrite their saved one) —
            # this loop has no saved preference to protect, but it does have
            # to follow both, and only handling the old name would leave
            # headless silently not retargeting when a device disappears.
            if event in ("real_sink_changed", "real_sink_replaced") and routing.real:
                try:
                    if not engine.retarget_output(routing.real.name):
                        engine.retarget(routing.monitor_source, routing.real.name)
                    print(f"\noutput -> {routing.real.name}")
                except Exception as e:  # noqa: BLE001 — don't limp along on a
                    # broken stream; stop cleanly (finally below restores
                    # the sink) rather than leaving state half-broken.
                    print(f"\nretarget failed, stopping: {e}")
                    break
            elif event == "real_sink_lost":
                print("output device lost, waiting...")
            elif event == "trap_lost":
                print("\nour audio device disappeared — stopping")
                break

            # Same protection the GUI gets (C8): a capture stream can be
            # alive, healthy and wired to the wrong thing, and a feedback
            # loop only gets worse while you decide what to do about it.
            ticks += 1
            if ticks % CAPTURE_CHECK_TICKS == 0:
                try:
                    state = routing.diagnose_capture(os.getpid())
                except Exception:  # noqa: BLE001
                    state = None
                if state == "feedback_loop":
                    print("\nfeedback loop detected (capturing our own "
                          "output) — stopping")
                    break
                if state in ("trap_lost", "capture_hijacked"):
                    print(f"\ncapture is wrong ({state}) — stopping")
                    break
            s = engine.stats
            print(f"\r{engine.latency_ms:5.1f} ms latency  "
                  f"{s.worker_ms_avg:5.1f} ms/block  blocks={s.blocks_in}"
                  f"  fallbacks={s.fallback_blocks}  xruns={s.xruns}   ",
                  end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        routing.disable()
        print("\nrestored")


if __name__ == "__main__":
    main()
