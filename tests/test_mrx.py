#!/usr/bin/env python3
"""MRX processor + the keep-effects switch — no audio devices needed.

The registry-name checks are pure and always run. The rest need
mrx.int8.onnx (scripts/fetch_mrx.py), so they report NOTHING TESTED rather
than passing vacuously.

What these pin down:

  * output sample 0 is input sample 0. MRX is the first non-causal processor
    here whose windows are built by hand, and the engine's positional dry/wet
    pairing assumes count alignment. A one-hop slip is inaudible in isolation
    and an echo at anything under 100% mix, so it is checked by searching the
    offsets around 0 and insisting 0 wins.

  * lag stays inside latency_samples, so _apply_caps' FIFO sizing holds.

  * the keep-effects switch actually changes the audio, and changes it by
    ADDING rather than by rescaling — a switch wired to nothing is the one
    failure mode this whole feature has to avoid.

  * chunk and advance are whole numbers of hops. If they are not, the three
    STFT resolutions stop agreeing on T and their outputs cannot be summed.
"""

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live import processors  # noqa: E402
from assassin_live.paths import models_dir  # noqa: E402
from assassin_live.processors.mrx import HOP  # noqa: E402

SR = 44100
BLOCK = 882          # 20 ms, what the engine feeds after resampling


def test_registry_names():
    parse = processors._mrx_chunk_ms
    assert parse("mrx") == processors._MRX_DEFAULT_MS
    assert parse("mrx_500ms") == 500
    assert parse("mrx_8000ms") == 8000
    for bad in ("mrx_ms", "mrx_0ms", "mrx_-1ms", "mrxms", "mrx_abcms",
                "spleeter_1000ms", "dpdfnet_hr", "passthrough"):
        assert parse(bad) is None, f"{bad!r} parsed as an mrx config"
    assert processors._files_for("mrx_2000ms") == ("mrx.int8.onnx",)
    print(f"  OK  mrx / mrx_<n>ms parsing, default "
          f"{processors._MRX_DEFAULT_MS} ms, variants {processors.MRX_CHUNKS_MS}")


def test_keeps_effects_predicate():
    """The UI asks this by name, with no engine running, to decide whether
    its switch can do anything."""
    for name in ("mrx", "mrx_500ms", "mrx_4000ms"):
        assert processors.keeps_effects(name), f"{name} should keep effects"
    for name in ("dpdfnet_hr", "stemgen", "gtcrn", "dtln", "passthrough",
                 "spleeter", "spleeter_1000ms", "speechdenoiser"):
        assert not processors.keeps_effects(name), f"{name} should not"
    print("  OK  keeps_effects() true for every mrx name, false for the rest")


def _signal(seconds=5.0):
    """A tone (music-like) with noise bursts (effect-like) on top."""
    rng = np.random.default_rng(3)
    t = np.arange(int(seconds * SR)) / SR
    x = (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for s in (0.8, 2.4, 4.0):
        i = int(s * SR)
        if i + 2000 <= len(x):
            x[i:i + 2000] += (rng.standard_normal(2000) * 0.5).astype(np.float32)
    return x


def _stream(proc, x):
    proc.reset()
    out = [proc.feed(x[i:i + BLOCK]) for i in range(0, len(x) - BLOCK, BLOCK)]
    return np.concatenate([c for c in out if len(c)]) if out else np.zeros(0, np.float32)


def test_hop_alignment(proc):
    assert proc._chunk % HOP == 0, f"chunk {proc._chunk} is not whole hops"
    assert proc._advance % HOP == 0, f"advance {proc._advance} is not whole hops"
    assert proc._edge % HOP == 0, f"edge {proc._edge} is not whole hops"
    assert proc._advance > 0
    assert proc.latency_samples == proc._advance + proc._edge
    print(f"  OK  chunk {proc._chunk}, advance {proc._advance}, "
          f"edge {proc._edge} all whole hops")


def test_lag_bounded(proc):
    x = _signal()
    pushed = ((len(x) - BLOCK) // BLOCK) * BLOCK
    y = _stream(proc, x)
    lag = pushed - len(y)
    assert 0 <= lag <= proc.latency_samples, \
        f"lag {lag} outside [0, {proc.latency_samples}]"
    assert np.abs(y).max() > 0, "silent output"
    assert np.isfinite(y).all(), "non-finite samples in output"
    print(f"  OK  lag {lag} within latency_samples {proc.latency_samples} "
          f"({proc.latency_ms:.0f} ms), output finite and non-silent")


def test_count_aligned(proc):
    """Output sample 0 must be input sample 0. Checked against the processor's
    own longer-chunk output, which is the closest thing to a reference that
    needs no second model: a slipped window scores worse at offset 0 than at
    the offset it actually slipped by."""
    x = _signal(8.0)
    y = _stream(proc, x)
    ref_proc = processors.create("mrx_4000ms", models_dir())
    ref = _stream(ref_proc, x)

    def esr(a, b):
        n = min(len(a), len(b))
        if n <= 0:
            return float("inf")
        e = float(np.sum((a[:n] - b[:n]) ** 2))
        s = float(np.sum(a[:n] ** 2))
        return 10 * np.log10(e / s) if s > 0 and e > 0 else float("-inf")

    span, scores = 3, {}
    n = min(len(ref), len(y)) - span * HOP
    for o in range(-span, span + 1):
        a = ref[max(0, o * HOP):max(0, o * HOP) + n]
        b = y[max(0, -o * HOP):max(0, -o * HOP) + n]
        scores[o] = esr(a, b)
    best = min(scores, key=scores.get)
    assert best == 0, f"best alignment is {best} hops, not 0: {scores}"
    margin = min(v for k, v in scores.items() if k != 0) - scores[0]
    assert margin > 3.0, f"offset 0 wins by only {margin:.1f} dB: {scores}"
    print(f"  OK  offset 0 is best by {margin:.1f} dB "
          f"(0: {scores[0]:.1f} dB, nearest other: {scores[0] + margin:.1f} dB)")


def test_switch_changes_audio(proc):
    """The point of the feature: on must differ from off, and differ by
    adding something rather than by turning the whole output down."""
    x = _signal()
    proc.set_keep_effects(False)
    speech = _stream(proc, x)
    proc.set_keep_effects(True)
    both = _stream(proc, x)
    n = min(len(speech), len(both))
    assert n > 0
    speech, both = speech[:n], both[:n]

    extra = both - speech
    e_sp = float(np.sum(speech ** 2))
    e_ex = float(np.sum(extra ** 2))
    assert e_ex > 0, "keep-effects on and off produced identical audio"
    rel = 10 * np.log10(e_ex / e_sp)
    assert rel > -40.0, f"effects stem is only {rel:.1f} dB of dialogue — inaudible"
    # Adding, not rescaling: a gain change would leave both/speech near
    # constant across the clip.
    ratio = np.abs(both) / (np.abs(speech) + 1e-9)
    assert ratio.std() > 0.01, "on/off differ by a constant gain, not a stem"
    assert proc.keeps_effects is True
    print(f"  OK  switch adds a stem at {rel:+.1f} dB relative to dialogue, "
          f"not a gain change")


def test_reset_clears(proc):
    x = _signal()
    proc.set_keep_effects(True)
    a = _stream(proc, x)
    b = _stream(proc, x)      # _stream resets first
    n = min(len(a), len(b))
    assert np.array_equal(a[:n], b[:n]), "reset() left state behind"
    print("  OK  reset() makes a repeated run bit-identical")


def main() -> int:
    print("registry, no model needed:")
    test_registry_names()
    test_keeps_effects_predicate()

    mdir = models_dir()
    if not processors.is_available("mrx", mdir):
        print(f"\nNOTHING TESTED — mrx unavailable from {mdir}")
        print("  Needs mrx.int8.onnx — run scripts/fetch_mrx.py.")
        print("  Set ALLOW_NO_MODELS=1 to accept that.")
        return 0 if os.environ.get("ALLOW_NO_MODELS") == "1" else 1

    proc = processors.create("mrx", mdir)
    print(f"\n{proc.name}: sr={proc.sample_rate} wants_stereo={proc.wants_stereo} "
          f"latency={proc.latency_ms:.0f} ms")
    test_hop_alignment(proc)
    test_lag_bounded(proc)
    test_switch_changes_audio(proc)
    test_reset_clears(proc)
    test_count_aligned(proc)
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
