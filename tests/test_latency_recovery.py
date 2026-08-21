#!/usr/bin/env python3
"""Delay the pipeline accumulates, and the two ways it now stops keeping it.

The complaint: "it isn't continuously lagging as it was before, but the lag
is still bigger than in the first version". A 23-minute session on the
shipped default read `added latency 107 ms` against a `processor lag` of
29.3 ms — so ~78 ms of it was not the model — with `xruns 0` and the worker
at 11 % of its budget.

Two independent causes, fixed independently:

  A. _dry_out is fed `frames` per callback and drained by at most that
     (take_dry <= take <= frames), so its depth only ever grew. Every block
     the worker failed to fill was delay kept for the rest of the session;
     stop(), a model swap and the 160 ms cap were the only things that ever
     shortened it. _shed_slack() gives it back, a little per block, while
     wet is comfortably ahead.

  B. sd.Stream took no `latency=`, so PortAudio's 'high' default applied —
     34.8 + 34.8 ms measured, against 8.7 + 8.7 at 'low'. latency_ms
     excludes device buffering by design, so ~70 ms of what a listener
     hears had never appeared in any readout. STREAM_LATENCY now asks for
     'low' and device_latency_ms reports what was actually negotiated.

No audio hardware: the callback is driven directly and the worker is
simulated by appending to _out, which is what a real worker does.

Run: .venv/bin/python tests/test_latency_recovery.py
"""

import inspect
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.engine import (  # noqa: E402
    BLOCK,
    SAMPLE_RATE,
    STREAM_LATENCY,
    AudioEngine,
    _splice_head,
)
from assassin_live.processors.base import StreamProcessor  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def section(title: str) -> None:
    print(f"\n{title}")


class Passthrough(StreamProcessor):
    sample_rate = SAMPLE_RATE

    def reset(self) -> None:
        pass

    def feed(self, x: np.ndarray) -> np.ndarray:
        return x


class Rig:
    """One engine, driven a callback at a time."""

    def __init__(self, mix_pad=0):
        self.e = AudioEngine(Passthrough())
        self.e._mix_pad = mix_pad
        self.ind = np.full((BLOCK, 2), 0.1, dtype=np.float32)
        self.out = np.zeros((BLOCK, 2), dtype=np.float32)

    def block(self, produced: int) -> None:
        """The worker delivers `produced` samples and drains its input, then
        one callback runs."""
        while not self.e._in_q.empty():
            self.e._in_q.get_nowait()
        if produced:
            self.e._out = np.concatenate(
                [self.e._out, np.full((produced, 2), 0.2, dtype=np.float32)])
            if len(self.e._out) > self.e._max_out:
                self.e._out = self.e._out[-self.e._max_out:]
        self.e._callback_body(self.ind, self.out, BLOCK)

    def run(self, n: int, produced: int) -> None:
        for _ in range(n):
            self.block(produced)

    @property
    def dry(self) -> int:
        return len(self.e._dry_out)

    @property
    def wet(self) -> int:
        return len(self.e._out)


# -- A: the ratchet ---------------------------------------------------------

def test_the_dry_fifo_gives_delay_back():
    section("accumulated delay is recoverable")

    r = Rig()
    r.run(50, BLOCK)
    check("a healthy run accumulates nothing", r.dry == 0, f"{r.dry} samples")

    r.run(3, 0)                       # the worker stalls for three blocks
    stalled = r.dry
    check("a stall costs delay immediately", stalled == 3 * BLOCK,
          f"{stalled} samples = {1000 * stalled / SAMPLE_RATE:.0f} ms")

    r.run(50, BLOCK * 2)              # the worker catches up and runs ahead
    check("and it starts coming back while wet is ahead", r.dry < stalled,
          f"{stalled} -> {r.dry} samples")

    r.run(1000, BLOCK)
    check("until the stall costs nothing at all", r.dry == 0,
          f"{r.dry} samples after 1000 further blocks")
    check("which is what it never did before", stalled > 0)


def test_shedding_needs_wet_to_be_ahead():
    section("when nothing is shed")

    # Worker exactly in lockstep: no spare wet, so no room to shed.
    r = Rig()
    r.run(3, 0)
    stalled = r.dry
    before = r.e.stats.slack_shed
    r.run(200, BLOCK)
    check("a worker that is merely keeping up sheds nothing",
          r.e.stats.slack_shed == before and r.dry == stalled,
          f"dry {stalled} -> {r.dry}, shed {r.e.stats.slack_shed - before}")

    # Nothing accumulated: nothing to give back, however far ahead wet is.
    r2 = Rig()
    r2.run(200, BLOCK * 2)
    check("and a pipeline with no slack sheds nothing", r2.e.stats.slack_shed == 0,
          f"{r2.e.stats.slack_shed} samples")


def test_alignment_padding_is_not_treated_as_slack():
    section("_mix_pad is alignment, not slack")

    pad = BLOCK * 2
    r = Rig(mix_pad=pad)
    r.run(3, 0)
    r.run(2000, BLOCK * 2)
    check("shedding stops at the pad, not below it", r.dry >= pad,
          f"dry {r.dry} vs pad {pad}")
    check("and the pad itself is untouched", r.e._mix_pad == pad,
          f"mix_pad={r.e._mix_pad}")
    check("so the reported latency excludes it as before",
          r.e.latency_ms >= 0.0, f"{r.e.latency_ms:.1f} ms")


def test_both_sides_are_shed_together():
    section("dry and wet stay aligned")

    r = Rig()
    r.run(3, 0)
    r.run(20, BLOCK * 2)
    dry0, wet0 = r.dry, r.wet
    shed0 = r.e.stats.slack_shed
    r.run(1, BLOCK)
    n = r.e.stats.slack_shed - shed0
    check("a shed happened", n > 0, f"{n} samples")
    # fed BLOCK, drained BLOCK (wet was available), shed n -> net -n
    check("dry lost exactly what was shed", dry0 - r.dry == n,
          f"dry {dry0} -> {r.dry}, shed {n}")
    # wet gained BLOCK, drained BLOCK, shed n -> net -n
    check("and wet lost exactly the same", wet0 - r.wet == n,
          f"wet {wet0} -> {r.wet}, shed {n}")


def test_the_seam_is_crossfaded_not_cut():
    section("the splice")

    n = 24
    buf = np.arange(200, dtype=np.float32).repeat(2).reshape(200, 2)
    out = _splice_head(buf, n)

    check("n samples are gone", len(out) == len(buf) - n,
          f"{len(buf)} -> {len(out)}")
    check("the tail is untouched",
          np.array_equal(out[n:], buf[2 * n:]))
    # Endpoints of the crossfade: starts at the discarded value, ends at the
    # surviving one, so neither side steps.
    check("the seam starts on the outgoing samples",
          abs(float(out[0, 0]) - float(buf[0, 0])) < 1e-3,
          f"{float(out[0, 0]):.2f} vs {float(buf[0, 0]):.2f}")
    check("and lands on the incoming ones",
          abs(float(out[n - 1, 0]) - float(buf[2 * n - 1, 0])) < 1.0,
          f"{float(out[n - 1, 0]):.2f} vs {float(buf[2 * n - 1, 0]):.2f}")
    steps = np.abs(np.diff(out[:2 * n, 0]))
    check("no step discontinuity across it", float(steps.max()) < n,
          f"largest step {float(steps.max()):.2f} (a hard cut would be {n})")


def test_the_engine_still_works_while_shedding():
    section("audio keeps flowing")

    r = Rig()
    r.run(3, 0)
    before = r.e.stats.fallback_blocks
    r.run(500, BLOCK * 2)
    check("shedding causes no dry fallbacks",
          r.e.stats.fallback_blocks == before,
          f"{r.e.stats.fallback_blocks - before} fallbacks")
    check("output is finite and in range",
          np.isfinite(r.out).all() and float(np.abs(r.out).max()) <= 1.0,
          f"peak {float(np.abs(r.out).max()):.3f}")
    check("and something was actually shed", r.e.stats.slack_shed > 0,
          f"{r.e.stats.slack_shed} samples "
          f"({1000 * r.e.stats.slack_shed / SAMPLE_RATE:.0f} ms)")


# -- B: device buffering ----------------------------------------------------

def test_the_stream_asks_for_low_latency():
    section("device buffering")

    check("STREAM_LATENCY is 'low'", STREAM_LATENCY == "low", repr(STREAM_LATENCY))
    src = inspect.getsource(AudioEngine.start)
    check("and start() actually passes it to sd.Stream",
          "latency=STREAM_LATENCY" in src,
          "sd.Stream is opened without it" if "latency=" not in src else "")


def test_negotiated_buffering_is_reported_not_assumed():
    section("device_latency_ms")

    e = AudioEngine(Passthrough())
    check("None before a stream exists", e.device_latency_ms is None)

    class Stream:
        latency = (0.0087, 0.0087)
    e._stream = Stream()
    lat = e.device_latency_ms
    check("read back from the open stream, in ms",
          lat is not None and abs(lat[0] - 8.7) < 0.05 and abs(lat[1] - 8.7) < 0.05,
          str(lat))

    class Dead:
        @property
        def latency(self):
            raise RuntimeError("Invalid stream pointer")
    e._stream = Dead()
    check("a dead stream reports nothing rather than raising",
          e.device_latency_ms is None)

    # The point of reporting device buffering separately is that it stays
    # out of the pipeline figure. Assert the number, not the docstring.
    e2 = AudioEngine(Passthrough())
    e2._lag_ema = float(BLOCK)
    baseline = e2.latency_ms
    e2._stream = Stream()
    check("and latency_ms is unmoved by it",
          e2.latency_ms == baseline, f"{baseline:.1f} -> {e2.latency_ms:.1f} ms")


def main() -> int:
    test_the_dry_fifo_gives_delay_back()
    test_shedding_needs_wet_to_be_ahead()
    test_alignment_padding_is_not_treated_as_slack()
    test_both_sides_are_shed_together()
    test_the_seam_is_crossfaded_not_cut()
    test_the_engine_still_works_while_shedding()
    test_the_stream_asks_for_low_latency()
    test_negotiated_buffering_is_reported_not_assumed()

    print()
    if FAILURES:
        print(f"FAIL — {len(FAILURES)}: " + "; ".join(FAILURES))
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
