#!/usr/bin/env python3
"""A stall upstream of the engine's own queues, and how it is caught.

When the audio callback (or the whole process) is held up, the capture
device keeps recording into its own ring buffer and a duplex stream then
reads one block per block from it, so the backlog never drains. Measured
2026-10-31 with a loopback click through a private null sink: one 400 ms
callback stall took the round trip from 71 ms to 452 ms for good. None of
it is in _out or _dry_out, so the engine's trims cannot reach it.

PortAudio's per-callback times show it: outputBufferDacTime minus
inputBufferAdcTime is the audio held in the device buffers. Each side swings
~20 ms in a sawtooth on the PipeWire ALSA plugin while the sum holds to
+-4 ms, which is why the sum is tracked. These tests drive the engine with
fake times; no audio hardware needed.

Run: .venv/bin/python tests/test_capture_resync.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio import engine as engine_mod  # noqa: E402
from assassin_live.audio.engine import (  # noqa: E402
    BLOCK, CAPTURE_EXCESS_MS, RESYNC_QUIET_S, SAMPLE_RATE, SETTLE_BLOCKS,
    AudioEngine)
from assassin_live.processors.base import StreamProcessor  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class Negate(StreamProcessor):
    name, sample_rate, latency_samples, wants_stereo = "neg", SAMPLE_RATE, 0, True

    def reset(self) -> None:
        pass

    def feed(self, x):
        return -x


class Times:
    """What PortAudio hands the callback, for a device holding `held` s."""

    def __init__(self, now: float, held: float, swing: float = 0.0):
        # each side may swing; their sum is `held`
        self.currentTime = now
        self.inputBufferAdcTime = now - (0.02 + swing)
        self.outputBufferDacTime = now + held - (0.02 + swing)


def run(eng, plan, process=True):
    """plan: list of (blocks, held_s, loud). Returns nothing; drives eng."""
    eng._running = True
    eng._wet_target = eng._wet_gain = 1.0
    eng.set_bandlimit(False)
    now = 100.0
    rng = np.random.default_rng(0)
    out = np.zeros((BLOCK, 2), dtype=np.float32)
    for blocks, held, loud in plan:
        for i in range(blocks):
            x = ((0.1 * rng.standard_normal((BLOCK, 2))) if loud
                 else np.zeros((BLOCK, 2))).astype(np.float32)
            swing = 0.01 * ((i % 50) / 50.0)       # a sawtooth, cancelled in the sum
            eng._callback(x, out, BLOCK, Times(now, held, swing), None)
            if process:
                while not eng._in_q.empty():
                    eng._process_block(eng._in_q.get_nowait())
            now += BLOCK / SAMPLE_RATE
    eng._running = False


def secs(s):
    return int(s * SAMPLE_RATE / BLOCK)


def test_healthy_stream_never_resyncs():
    print("\na healthy stream, sound then a long silence")
    eng = AudioEngine(Negate())
    run(eng, [(secs(5), 0.07, True), (secs(10), 0.07, False)])
    check("no excess over the floor",
          eng.capture_backlog_ms < 5, f"{eng.capture_backlog_ms:.1f} ms")
    check("so no resync, however long the quiet", not eng.needs_resync)


def test_a_stall_waits_for_quiet_then_resyncs():
    print("\na 400 ms stall leaves the device buffers holding more")
    eng = AudioEngine(Negate())
    run(eng, [(secs(3), 0.07, True), (secs(3), 0.47, True)])
    check("the excess is seen",
          eng.capture_backlog_ms > CAPTURE_EXCESS_MS, f"{eng.capture_backlog_ms:.0f} ms")
    check("but not acted on while there is sound", not eng.needs_resync)
    run(eng, [(secs(RESYNC_QUIET_S) - 5, 0.47, False)])
    check("nor before the quiet has lasted long enough", not eng.needs_resync)
    run(eng, [(10, 0.47, False)])
    check(f"after {RESYNC_QUIET_S:.0f} s of quiet: resync", eng.needs_resync)


def test_a_small_drift_is_left_alone():
    print("\nan extra 50 ms is below the threshold")
    eng = AudioEngine(Negate())
    run(eng, [(secs(3), 0.07, True), (secs(5), 0.12, False)])
    check("not a stall", not eng.needs_resync, f"{eng.capture_backlog_ms:.0f} ms")


def test_the_first_second_is_ignored():
    print("\na new stream's buffers read low while filling")
    eng = AudioEngine(Negate())
    run(eng, [(SETTLE_BLOCKS - 5, 0.005, False), (secs(5), 0.07, False)])
    check("the low start does not become the floor",
          eng.capture_backlog_ms < 5, f"{eng.capture_backlog_ms:.1f} ms")
    check("so no resync", not eng.needs_resync)


def test_no_timing_info_means_no_resync():
    print("\na host API that leaves the times at zero")

    class Zero:
        currentTime = inputBufferAdcTime = outputBufferDacTime = 0.0

    eng = AudioEngine(Negate())
    eng._running = True
    out = np.zeros((BLOCK, 2), dtype=np.float32)
    for _ in range(secs(6)):
        eng._callback(np.zeros((BLOCK, 2), np.float32), out, BLOCK, Zero(), None)
    eng._running = False
    check("nothing tracked", eng.capture_backlog_ms == 0.0)
    check("never asks for a resync", not eng.needs_resync)


def test_quiet_dry_blocks_are_not_fallbacks():
    print("\nan empty wet FIFO during silence is not a fallback")
    eng = AudioEngine(Negate())
    run(eng, [(secs(2), 0.07, False)], process=False)
    check("silence: none counted", eng.stats.fallback_blocks == 0,
          f"{eng.stats.fallback_blocks}")
    run(eng, [(20, 0.07, True)], process=False)
    check("sound with nothing processed: counted",
          eng.stats.fallback_blocks >= 19, f"{eng.stats.fallback_blocks}")


def test_lag_fft_is_warmed_before_streaming():
    print("\nthe lag measurement's first-call cost is paid at start")
    engine_mod._lag_fft_warm = False
    AudioEngine(Negate())._warm_up(0.02)
    check("warmed by _warm_up", engine_mod._lag_fft_warm)


def test_stereo_models_are_not_measuring_forever():
    print("\na wants_stereo model never measures, so it must not say it is")
    check("lag_state says not needed",
          AudioEngine(Negate()).lag_state == "not needed")


def main() -> int:
    print("capture-side stalls and the resync (no audio hardware required)")
    test_healthy_stream_never_resyncs()
    test_a_stall_waits_for_quiet_then_resyncs()
    test_a_small_drift_is_left_alone()
    test_the_first_second_is_ignored()
    test_no_timing_info_means_no_resync()
    test_quiet_dry_blocks_are_not_fallbacks()
    test_lag_fft_is_warmed_before_streaming()
    test_stereo_models_are_not_measuring_forever()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
