#!/usr/bin/env python3
"""The real AudioEngine against processors that do not answer every block.
No audio hardware needed.

Why this exists: tests/bench_quality.py re-implements the chain instead of
driving the engine, so anything that lives in the engine's FIFOs is invisible
to every quality number. Two such defects were found by reading the code and
confirmed here (2026-10-22):

  * The wet FIFO was capped at a fixed 160 ms. A chunked processor (Spleeter,
    ROADMAP A1) answers with a whole chunk at once; the worker kept the newest
    160 ms of each 1 s chunk and the callback filled the rest with dry. Before
    the fix: 16 % of the output processed, 84 % the original music.
  * Trimming either FIFO at its cap dropped samples from one side of a pairing
    that is positional, leaving dry and wet apart for the rest of the session
    (an echo at any mix below 100 %).

The engine is driven one block at a time with an infinitely fast worker:
_callback() for the device side, then _process_block() for every queued
block. Deterministic, and it exercises the engine's own code, not a copy.

Run: .venv/bin/python tests/test_engine_chunked.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.engine import (  # noqa: E402
    BASE_FIFO_BLOCKS, BLOCK, SAMPLE_RATE, AudioEngine)
from assassin_live.processors.base import StreamProcessor  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class Chunked(StreamProcessor):
    """Stereo, 48 kHz, answers once per `chunk` samples with the NEGATED
    input — so wet is distinguishable from dry, and a correctly aligned
    50/50 mix of the two cancels to silence."""
    name = "fake_chunked"
    sample_rate = SAMPLE_RATE
    wants_stereo = True

    def __init__(self, chunk: int, declared: int | None = None):
        self.chunk = chunk
        self.latency_samples = chunk if declared is None else declared
        self.reset()

    def reset(self) -> None:
        self._buf = np.zeros((0, 2), dtype=np.float32)

    def feed(self, x: np.ndarray) -> np.ndarray:
        self._buf = np.concatenate([self._buf, x.astype(np.float32)])
        out = []
        while len(self._buf) >= self.chunk:
            out.append(-self._buf[:self.chunk])
            self._buf = self._buf[self.chunk:]
        return np.concatenate(out) if out else np.zeros((0, 2), dtype=np.float32)


class Stalling(StreamProcessor):
    """Block-synchronous negation that withholds everything for `stall`
    blocks, then releases it all at once — a worker hiccup in miniature.
    Declares zero latency, so the burst overruns the base cap."""
    name = "fake_stall"
    sample_rate = SAMPLE_RATE
    wants_stereo = True
    latency_samples = 0

    def __init__(self, stall_from: int, stall_blocks: int):
        self.stall_from, self.stall_blocks = stall_from, stall_blocks
        self.reset()

    def reset(self) -> None:
        self._n = 0
        self._held = np.zeros((0, 2), dtype=np.float32)

    def feed(self, x: np.ndarray) -> np.ndarray:
        self._n += 1
        self._held = np.concatenate([self._held, -x.astype(np.float32)])
        if self.stall_from <= self._n < self.stall_from + self.stall_blocks:
            return np.zeros((0, 2), dtype=np.float32)
        out, self._held = self._held, np.zeros((0, 2), dtype=np.float32)
        return out


def drive(eng: AudioEngine, blocks: np.ndarray, mix: float) -> np.ndarray:
    """Run (nblocks, BLOCK, 2) input through the engine; return the output."""
    eng._running = True          # no stream, no worker thread: we are both
    eng._wet_target = eng._wet_gain = mix
    eng.set_bandlimit(False)     # a stateful IIR would break the cancellation
    out = np.zeros_like(blocks)
    for i, x in enumerate(blocks):
        eng._callback(x, out[i], BLOCK, None, None)
        while not eng._in_q.empty():
            eng._process_block(eng._in_q.get_nowait())
    eng._running = False
    return out.reshape(-1, 2)


def test_chunked_output_is_processed():
    print("\nchunked processors: share of output that is processed")
    n = 500                                       # 10 s
    x = np.full((n, BLOCK, 2), 0.5, dtype=np.float32)
    for chunk_ms in (20, 100, 1000, 4000):
        chunk = SAMPLE_RATE * chunk_ms // 1000
        eng = AudioEngine(Chunked(chunk))
        y = drive(eng, x, mix=1.0)
        start = 2 * chunk                         # past the fill-up
        share = float(np.mean(y[start:, 0] < 0))
        check(f"{chunk_ms} ms chunks: all output processed after fill-up",
              share > 0.999,
              f"{100 * share:.1f}% wet, {eng.stats.fallback_blocks} fallback blocks, "
              f"{eng.stats.wet_trimmed} wet samples trimmed")


def test_chunked_mix_stays_aligned():
    print("\nchunked processors: dry and wet are the same instant")
    rng = np.random.default_rng(0)
    n = 400
    x = (0.1 * rng.standard_normal((n, BLOCK, 2))).astype(np.float32)
    for chunk_ms in (100, 1000):
        chunk = SAMPLE_RATE * chunk_ms // 1000
        eng = AudioEngine(Chunked(chunk))
        y = drive(eng, x, mix=0.5)
        tail = y[3 * chunk:]
        resid = float(np.sqrt(np.mean(tail ** 2)))
        check(f"{chunk_ms} ms chunks: 50/50 mix of x and -x cancels",
              resid < 1e-6, f"residual RMS {resid:.2e} (input 1e-1)")


def test_trim_keeps_lockstep():
    print("\na stall long enough to hit both FIFO caps")
    rng = np.random.default_rng(1)
    n = 200
    x = (0.1 * rng.standard_normal((n, BLOCK, 2))).astype(np.float32)
    stall = 3 * BASE_FIFO_BLOCKS                  # well past the 160 ms cap
    eng = AudioEngine(Stalling(stall_from=20, stall_blocks=stall))
    y = drive(eng, x, mix=0.5)
    check("the stall actually overran the wet cap",
          eng.stats.wet_trimmed > 0, f"{eng.stats.wet_trimmed} samples trimmed")
    after = y[(20 + stall + 2 * BASE_FIFO_BLOCKS) * BLOCK:]
    resid = float(np.sqrt(np.mean(after ** 2)))
    check("after the stall, dry and wet are still paired by instant",
          resid < 1e-6, f"residual RMS {resid:.2e} (misaligned would be ~1e-1)")


def test_caps_follow_the_processor():
    print("\nFIFO caps are sized from the installed processor")
    eng = AudioEngine(Chunked(BLOCK))
    small = (eng._max_out, eng._in_q.maxsize)
    check("a one-block processor keeps roughly the old 160 ms",
          small[0] == BLOCK * BASE_FIFO_BLOCKS + 2 * BLOCK,
          f"_max_out={small[0]}, queue={small[1]}")
    eng.set_processor(Chunked(SAMPLE_RATE))       # not running -> inline swap
    big = (eng._max_out, eng._in_q.maxsize)
    check("a 1 s chunk processor gets room for two chunks",
          big[0] >= 2 * SAMPLE_RATE, f"_max_out={big[0]}")
    check("…and an input queue that outlasts a chunk's inference",
          big[1] >= SAMPLE_RATE // BLOCK, f"queue={big[1]}")


class MonoNegate(StreamProcessor):
    """The shipped shape: mono in, mono out, block-synchronous, negated."""
    name = "fake_mono"
    sample_rate = SAMPLE_RATE
    latency_samples = 0

    def reset(self) -> None:
        pass

    def feed(self, x: np.ndarray) -> np.ndarray:
        return -x.astype(np.float32)


def drive_toggling(eng: AudioEngine, blocks: np.ndarray, mix: float,
                   toggles: dict) -> np.ndarray:
    """drive(), switching the mask shaper's smoothing at given block indices."""
    eng._running = True
    eng._wet_target = eng._wet_gain = mix
    eng.set_bandlimit(False)
    out = np.zeros_like(blocks)
    for i, x in enumerate(blocks):
        if i in toggles:
            eng.set_smoothing(toggles[i])
        eng._callback(x, out[i], BLOCK, None, None)
        while not eng._in_q.empty():
            eng._process_block(eng._in_q.get_nowait())
    eng._running = False
    return out.reshape(-1, 2)


def test_shaper_toggle_keeps_lockstep():
    print("\nmask shaper switched on and off mid-stream (ROADMAP C12)")
    rng = np.random.default_rng(2)
    n = 400
    # -x has |Wet| == |Dry| in every bin, so the shaper's target gain is 1
    # and the wet comes through unchanged: any residual in a 50/50 mix is
    # misalignment, not shaping.
    mono = (0.1 * rng.standard_normal((n, BLOCK, 1))).astype(np.float32)
    x = np.repeat(mono, 2, axis=2)                # L == R, so mono -x cancels too
    toggles = {100: 80.0, 200: 0.0, 300: 120.0}
    for label, proc in (("stereo path", Chunked(BLOCK)), ("mono path", MonoNegate())):
        eng = AudioEngine(proc)
        eng._mask_lag = 0     # the mono path waits for a measured lag; this one is known
        y = drive_toggling(eng, x, 0.5, toggles)
        # One block either side of each switch may fall back to dry while the
        # shaper fills (on) — that is a gap, not a misalignment, and it is
        # bounded by the shaper's own framing.
        settled = np.ones(n, dtype=bool)
        settled[:4] = False
        for b in toggles:
            settled[b - 1:b + 2] = False
        y_b = y.reshape(n, BLOCK, 2)
        resid = float(np.sqrt(np.mean(y_b[settled] ** 2)))
        gap = float(np.sqrt(np.mean(y_b[~settled][4:] ** 2))) if (~settled).sum() > 4 else 0.0
        check(f"{label}: 50/50 cancels in every settled block across three switches",
              resid < 1e-5, f"residual RMS {resid:.2e} (input 1e-1)")
        check(f"{label}: the shaper was really in the chain",
              eng.shaping_active, "smoothing 120 ms at the end")
        check(f"{label}: switching costs at most a fallback block, not a drift",
              gap < 0.1, f"transition RMS {gap:.2e}, "
              f"{eng.stats.fallback_blocks} fallback blocks")


def main() -> int:
    print("engine vs chunked / stalling processors (no audio hardware required)")
    test_chunked_output_is_processed()
    test_chunked_mix_stays_aligned()
    test_trim_keeps_lockstep()
    test_caps_follow_the_processor()
    test_shaper_toggle_keeps_lockstep()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
