#!/usr/bin/env python3
"""Live model switching, and the supervisor that has to survive it.

Three defects, all found by reading rather than by a repro, all of which
produce the same user-facing complaint — "the audio is out of sync" or
"it echoes and gets louder":

  * AudioEngine.set_processor() reset none of the fifteen pieces of state
    that start() resets before a model is first allowed to see audio. The
    new model inherited the old one's measured lag, its alignment padding,
    its band-limit IIR state and its queued audio, and was never warmed —
    so a switch baked in the cold-start latency that start()'s _warm_up()
    exists to prevent, permanently, and blended dry against wet up to
    ~45 ms apart (gtcrn ~25 ms vs dpdfnet_hr ~70 ms).

  * App._tick() had no _busy guard. _work_off() runs off the Tk thread and
    cannot clear self.enabled, so a tick landing mid-stop saw enabled=True
    and a closed stream, and "recovered" it — opening a fresh stream that
    routing.disable() then destroyed the trap sink underneath. That is the
    2026-08-18 feedback loop verbatim (backends/pipewire.py
    diagnose_capture), and _poll_transition drops the engine reference
    immediately after, so nothing was left that could stop it.

  * Five of _tick()'s exits returned without rescheduling, and the loop is
    armed only once, in __init__. The first silent stream death, lost sink,
    lost trap or detected feedback loop ended supervision for the life of
    the process. The worst of the five is the feedback detector itself:
    the first loop turned the app off and disabled the only thing that can
    catch the second.

No Tk, no audio hardware, no PipeWire. The App half is built with
object.__new__ (see test_status_line.py for why); the engine half never
calls start(), so no stream is ever opened.

Run: .venv/bin/python tests/test_live_model_swap.py
"""

import sys
import threading
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.engine import BLOCK, SAMPLE_RATE, AudioEngine  # noqa: E402
from assassin_live.processors.base import StreamProcessor  # noqa: E402
from assassin_live.ui.app import App  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def section(title: str) -> None:
    print(f"\n{title}")


# -- doubles ----------------------------------------------------------------

class FakeProc(StreamProcessor):
    """Passthrough at the engine's own rate, so no resamplers are built and
    the swap under test is the only thing moving."""

    sample_rate = SAMPLE_RATE

    def __init__(self, name: str):
        self.name = name
        self.resets = 0
        self.fed = 0

    def reset(self) -> None:
        self.resets += 1

    def feed(self, x: np.ndarray) -> np.ndarray:
        self.fed += len(x)
        return x


class FakeRoot:
    """Records after() calls instead of scheduling them."""

    def __init__(self):
        self.scheduled = []

    def after(self, ms, fn):
        self.scheduled.append((ms, fn))


class FakeEngine:
    def __init__(self, stream_ok=True):
        self.stream_ok = stream_ok
        self.retargets = []

    def retarget(self, monitor_source, sink_name):
        self.retargets.append((monitor_source, sink_name))

    def stop(self):
        pass


class FakeSink:
    def __init__(self, name):
        self.name = name
        self.description = name


class FakeRouting:
    monitor_source = "MusicAssassin.monitor"

    def __init__(self, real="hw-sink"):
        self.real = FakeSink(real) if real else None
        self.disabled = 0

    def disable(self):
        self.disabled += 1


def make_app(busy=False, enabled=True, engine=None, real="hw-sink"):
    a = object.__new__(App)
    a.root = FakeRoot()
    a._busy = busy
    a.enabled = enabled
    a.engine = engine
    a.routing = FakeRouting(real)
    a.said = []
    a._say = lambda msg, colour=None, hold=True: a.said.append(msg)
    a._paint_button = lambda *_: None
    a._result = None
    return a


# -- the supervisor stays alive ---------------------------------------------

def test_the_heartbeat_survives_every_exit():
    section("supervision cannot stop itself")

    a = make_app()
    a._tick_body = lambda: None
    a._tick()
    check("a clean pass reschedules", a.root.scheduled == [(1000, a._tick)],
          f"{len(a.root.scheduled)} scheduled")

    a = make_app()
    def boom():
        raise RuntimeError("graph read failed")
    a._tick_body = boom
    raised = False
    try:
        a._tick()
    except RuntimeError:
        raised = True
    check("an exception out of the body still reschedules",
          a.root.scheduled == [(1000, a._tick)],
          f"{len(a.root.scheduled)} scheduled, propagated={raised}")

    a = make_app()
    a._tick_body = lambda: None
    for _ in range(3):
        a._tick()
    check("and it does so exactly once per tick", len(a.root.scheduled) == 3,
          f"{len(a.root.scheduled)} after 3 ticks")

    # The regression that mattered: a reschedule living at an exit rather
    # than in the finally is one `return` away from being skipped again.
    import inspect
    body = inspect.getsource(App._tick_body)
    check("the body owns no reschedule of its own",
          "self._tick" not in body,
          "found a self._tick reference inside _tick_body"
          if "self._tick" in body else "")


def test_a_transition_in_flight_is_left_alone():
    section("no supervision during a transition")

    dead = FakeEngine(stream_ok=False)
    a = make_app(busy=True, engine=dead)
    a._tick_body()
    check("a dead stream is not 'recovered' while stopping",
          dead.retargets == [],
          f"retarget called {len(dead.retargets)}x")

    # Same engine, same dead stream, transition finished: now it must act,
    # or the guard would have replaced one bug with another.
    dead2 = FakeEngine(stream_ok=False)
    a = make_app(busy=False, engine=dead2)
    a._tick_body()
    check("but it still recovers once the transition is done",
          dead2.retargets == [("MusicAssassin.monitor", "hw-sink")],
          str(dead2.retargets))


def test_recovery_needs_somewhere_to_recover_to():
    section("recovery without an output sink")

    dead = FakeEngine(stream_ok=False)
    a = make_app(engine=dead, real=None)
    a._tick_body()
    check("no retarget into an unknown sink", dead.retargets == [],
          f"retarget called {len(dead.retargets)}x")
    check("turned off instead", a.enabled is False and a.engine is None)
    check("and said so", any("turned off" in m for m in a.said),
          a.said[-1] if a.said else "(silent)")


# -- the model swap ---------------------------------------------------------

def test_a_swap_with_no_stream_happens_inline():
    section("set_processor with nothing running")

    old, new = FakeProc("old"), FakeProc("new")
    e = AudioEngine(old)
    e.set_processor(new)
    check("installed immediately", e.proc is new, e.proc.name)
    check("nothing deferred", e._pending_runtime is None)


def test_a_live_swap_is_handed_to_the_worker():
    section("set_processor while streaming")

    old, new = FakeProc("old"), FakeProc("new")
    e = AudioEngine(old)
    e._running = True
    e.set_processor(new)
    check("the caller's thread does not install it", e.proc is old, e.proc.name)
    check("it is queued for the worker", e._pending_runtime is new)

    e._swap_runtime(e._pending_runtime)
    e._pending_runtime = None
    check("and the worker installs it", e.proc is new, e.proc.name)
    check("warmed before going live, not on the first real block",
          new.fed > 0, f"{new.fed} samples of silence pushed")


def test_the_worker_picks_up_a_queued_swap():
    section("the worker's pickup")

    old, new = FakeProc("old"), FakeProc("new")
    e = AudioEngine(old)
    e._running = True
    worker = threading.Thread(target=e._work, daemon=True)
    worker.start()
    try:
        e.set_processor(new)
        # No audio is fed: the pickup is at the top of the loop, so it must
        # not wait on a block that a paused source will never send.
        for _ in range(500):
            if e.proc is new:
                break
            threading.Event().wait(0.01)
    finally:
        e._running = False
        worker.join(timeout=2)

    check("the worker installs a queued swap", e.proc is new, e.proc.name)
    check("without waiting for a block to arrive", new.fed > 0,
          f"{new.fed} samples (warm-up only — nothing was fed)")
    check("and clears the slot behind it", e._pending_runtime is None)


def test_a_swap_discards_what_belonged_to_the_old_model():
    section("what a swap resets")

    old, new = FakeProc("old"), FakeProc("new")
    e = AudioEngine(old)
    e._running = True

    # state a session accumulates, all of it about `old`
    e._mask_lag = 2400
    e._pending_lag = 2400
    e._mix_pad_pending = 2400
    e._lag_given_up = True
    e._measuring = True
    e._dry_work = np.ones((BLOCK * 3, 2), dtype=np.float32)
    e._out = np.ones((BLOCK * 3, 2), dtype=np.float32)
    for _ in range(4):
        e._in_q.put_nowait(np.ones((BLOCK, 2), dtype=np.float32))
    gen_before = e._lag_gen

    e._swap_runtime(new)

    check("the measured lag is dropped, not inherited", e._mask_lag is None,
          f"mask_lag={e._mask_lag}")
    check("and so is a lag answer still pending", e._pending_lag is None)
    check("and the mix-path padding it would have applied",
          e._mix_pad_pending is None)
    check("the estimator is allowed to try again",
          e._lag_given_up is False and e._measuring is False)
    check("the rebuild's dry history is dropped", len(e._dry_work) == 0,
          f"{len(e._dry_work)} samples")
    check("the old model's processed audio is not spliced on",
          len(e._out) == 0, f"{len(e._out)} samples")
    check("nor is its queued input fed to the new model",
          e._in_q.qsize() == 0, f"{e._in_q.qsize()} blocks")
    check("in-flight measurements are invalidated", e._lag_gen > gen_before,
          f"{gen_before} -> {e._lag_gen}")
    check("and the callback is told to clear its own FIFO",
          e._fifo_reset_pending is True)


def test_the_callback_drops_the_old_alignment():
    section("the callback half of a swap")

    e = AudioEngine(FakeProc("p"))
    frames = BLOCK
    # a session mid-flight: a padded dry FIFO and a measured lag
    e._dry_out = np.ones((frames * 4, 2), dtype=np.float32)
    e._mix_pad = frames * 2
    e._lag_ema = 5000.0
    e._fifo_reset_pending = True

    indata = np.full((frames, 2), 0.25, dtype=np.float32)
    outdata = np.zeros((frames, 2), dtype=np.float32)
    e._callback_body(indata, outdata, frames)

    check("the flag is consumed once", e._fifo_reset_pending is False)
    check("the old model's padding is gone", e._mix_pad == 0,
          f"mix_pad={e._mix_pad}")
    check("the dry FIFO holds only audio fed after the swap",
          len(e._dry_out) == frames, f"{len(e._dry_out)} samples, expected {frames}")
    check("no stale sample survived it",
          float(e._dry_out.max()) == 0.25, f"max={float(e._dry_out.max())}")
    check("and the latency reading restarts rather than decaying down",
          e._lag_ema < 5000.0, f"lag_ema={e._lag_ema:.1f}")


def test_a_measurement_cannot_outlive_its_model():
    section("a lag measurement racing a swap")

    class BlockingEstimator:
        """Holds the measuring thread open until the swap has happened."""

        exhausted = False

        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()

        def push(self, dry, wet):
            return True

        def take_window(self):
            z = np.zeros(64, dtype=np.float32)
            return z, z

        def measure(self, d, w):
            self.entered.set()
            self.release.wait(5.0)
            return 2400          # the OLD model's lag

    e = AudioEngine(FakeProc("old"))
    e._running = True
    est = BlockingEstimator()
    e._lag_est = est

    dry = np.zeros((64, 2), dtype=np.float32)
    wet = np.zeros(64, dtype=np.float32)
    e._collect_lag(dry, wet)
    check("a measurement is running", est.entered.wait(5.0))

    e._swap_runtime(FakeProc("new"))
    est.release.set()
    for _ in range(500):                      # let the thread finish
        if not est.release.is_set() or e._pending_lag is not None:
            break
        threading.Event().wait(0.01)
        if e._measuring is False and e._pending_lag is None:
            break

    check("its answer is discarded", e._pending_lag is None,
          f"pending_lag={e._pending_lag}")
    check("and it does not clear the flag the swap already cleared",
          e._measuring is False)
    check("so the new model measures for itself", e._mask_lag is None)


def main() -> int:
    test_the_heartbeat_survives_every_exit()
    test_a_transition_in_flight_is_left_alone()
    test_recovery_needs_somewhere_to_recover_to()
    test_a_swap_with_no_stream_happens_inline()
    test_a_live_swap_is_handed_to_the_worker()
    test_the_worker_picks_up_a_queued_swap()
    test_a_swap_discards_what_belonged_to_the_old_model()
    test_the_callback_drops_the_old_alignment()
    test_a_measurement_cannot_outlive_its_model()

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}: " + "; ".join(FAILURES))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
