#!/usr/bin/env python3
"""The mask shaper (audio/maskshape.py): suppression floor and smoothing on
the chain's own per-bin decision. No audio hardware, no models.

The property that matters most is the one its predecessor lacked: smoothing
must never amplify. bench_quality.shape_mask() smoothed the |Wet|/|Dry| ratio
itself, and a single bin where Dry was near zero left a clipped 8x reading
that survived the release window and burst out of a later, normal frame
(HANDOVER, "The permissiveness knob", bug 3 — never resolved there). This
shaper smooths powers instead and caps the gain at 1; the burst test below
rebuilds that scenario and checks no frame comes out louder than its dry.

Run: .venv/bin/python tests/test_mask_shaper.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.maskshape import HOP, N_FFT, MaskShaper  # noqa: E402

SR = 48000
FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def run(sh: MaskShaper, dry_st: np.ndarray, wet: np.ndarray, block: int = 960):
    """Stream through in engine-sized blocks (odd sizes on purpose elsewhere)."""
    o_st, o_w = [], []
    for i in range(0, len(wet), block):
        st, w = sh.process(dry_st[i:i + block], wet[i:i + block])
        o_st.append(st)
        o_w.append(w)
    return np.concatenate(o_st), np.concatenate(o_w)


def noise(n: int, seed: int, scale: float = 0.1) -> np.ndarray:
    return (scale * np.random.default_rng(seed).standard_normal((n, 2))).astype(np.float32)


def rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def test_count_aligned():
    print("\ncount alignment (the engine's dry/wet pairing depends on it)")
    sh = MaskShaper(SR)
    sh.set_params(floor_db=12, release_ms=80)
    sizes = [17, 300, 960, 1, 4096, 333] * 20   # deliberately ragged
    d = noise(sum(sizes), 0)
    w = 0.5 * d.mean(axis=1)
    o_st, o_w = [], []
    i = 0
    for size in sizes:
        st, ww = sh.process(d[i:i + size], w[i:i + size])
        o_st.append(st)
        o_w.append(ww)
        i += size
    st, ww = np.concatenate(o_st), np.concatenate(o_w)
    check("as many dry samples out as wet", len(st) == len(ww), f"{len(st)} / {len(ww)}")
    check("the dry handed back is the input dry, in order",
          np.array_equal(st, d[:len(st)]))
    check("holds at most the framing plus one hop",
          i - len(ww) < N_FFT, f"held {i - len(ww)} of {i}")


def test_transparent_where_nothing_to_reshape():
    print("\nno reshaping wanted -> wet passes through")
    sh = MaskShaper(SR)
    sh.set_params(floor_db=40, release_ms=100)    # floor well below the 0.5 mask
    d = noise(SR, 1)
    w = 0.5 * d.mean(axis=1)                      # a flat -6 dB "decision"
    _, out = run(sh, d, w)
    ref = w[:len(out)]
    err = rms(out[N_FFT:] - ref[N_FFT:]) / rms(ref[N_FFT:])
    check("output equals wet to within 1e-4", err < 1e-4, f"relative error {err:.1e}")


def test_floor():
    print("\nfloor: a bin the model removed comes back at the floor")
    sh = MaskShaper(SR)
    sh.set_params(floor_db=12)
    d = noise(SR, 2)
    w = np.zeros(SR, dtype=np.float32)            # model removed everything
    _, out = run(sh, d, w)
    want = 10 ** (-12 / 20) * rms(d.mean(axis=1))
    got = rms(out[N_FFT:])
    check("output sits at −12 dB of the dry", abs(20 * np.log10(got / want)) < 0.5,
          f"{20 * np.log10(got / rms(d.mean(axis=1))):.2f} dB re dry")


def test_release_holds_the_gain():
    print("\nrelease: a gain that collapses is let down slowly")
    d = noise(2 * SR, 3)
    dm = d.mean(axis=1)
    w = dm.copy()
    w[SR:] = 0.0                                  # the model cuts at t = 1 s
    res = {}
    for ms in (0, 100):
        sh = MaskShaper(SR)
        sh.set_params(release_ms=ms, floor_db=0 if ms else 60)  # non-identity either way
        _, out = run(sh, d, w)
        res[ms] = [rms(out[SR + a:SR + a + 960]) / rms(dm[SR + a:SR + a + 960])
                   for a in (2400, 9600, 48000 - 960)]   # +50 ms, +200 ms, ~+1 s
    fast, slow = res[0], res[100]
    check("without smoothing the cut is immediate", fast[0] < 0.05, f"{fast[0]:.3f} at +50 ms")
    check("with 100 ms release the gain is still well up at +50 ms",
          slow[0] > 0.3, f"{slow[0]:.3f}")
    check("…and gone a second later", slow[2] < 0.05, f"{slow[2]:.3f}")


def test_no_burst_from_a_silent_bin():
    print("\nthe bug its predecessor had: no amplification from a near-silent dry")
    n = 3 * SR
    rng = np.random.default_rng(4)
    d = noise(n, 5)
    # A loud dry with intermittent 40 ms holes, during which the 'model'
    # leaves residual energy — the division-by-near-zero case — followed by
    # perfectly normal frames, which is where the stale value used to burst.
    w = 0.3 * d.mean(axis=1) + 0.02 * rng.standard_normal(n).astype(np.float32)
    holes = range(SR // 4, n, SR // 2)
    for start in holes:
        d[start:start + 1920] = 0.0
    sh = MaskShaper(SR)
    sh.set_params(release_ms=150, attack_ms=0)
    o_st, out = run(sh, d, w)
    away, edge = [], []
    for i in range(N_FFT, len(out) - HOP, HOP):
        dr = rms(o_st[i:i + HOP].mean(axis=1))
        if dr <= 1e-3:
            continue
        near = any(-N_FFT <= i - s <= 1920 + N_FFT for s in holes)
        (edge if near else away).append(rms(out[i:i + HOP]) / dr)
    # Away from a silence edge is where the old bug burst: a stale reading
    # held through the release and divided back out, frames after its cause.
    check("no frame away from a silence edge comes out louder than its dry",
          max(away) <= 1.02, f"worst out/dry {max(away):.3f} over {len(away)} frames")
    # Within one analysis window of an edge, a 256-sample hop's RMS is not a
    # fair dry reference: the 512-sample window spreads energy across the
    # boundary for ANY gain that is not exactly 1 — a stateless gain held
    # between 0.89 and 1.0 reads 1.15 here (measured 2026-10-22). The ceiling
    # only has to catch a real burst, which the old 8x clip produced.
    check("at silence edges, nothing beyond window smear",
          max(edge) <= 1.5, f"worst out/dry {max(edge):.3f} over {len(edge)} frames")


def test_stereo_wet():
    print("\nstereo wet (a wants_stereo separator): same correction on both channels")
    sh = MaskShaper(SR)
    sh.set_params(floor_db=40)
    d = noise(SR, 6)
    w = 0.5 * d                                   # keeps the image
    st, out = run(sh, d, w)
    ref = w[:len(out)]
    err = rms(out[N_FFT:] - ref[N_FFT:]) / rms(ref[N_FFT:])
    check("output keeps both channels as the separator made them",
          out.ndim == 2 and err < 1e-4, f"relative error {err:.1e}")
    sh = MaskShaper(SR)
    sh.set_params(floor_db=12)
    _, out = run(sh, d, np.zeros_like(d))
    l, r = rms(out[N_FFT:, 0]), rms(out[N_FFT:, 1])
    check("the floor's make-up comes from each dry channel",
          abs(l - r) / max(l, r) < 0.1 and np.corrcoef(out[N_FFT:, 0], out[N_FFT:, 1])[0, 1] < 0.2,
          f"L {l:.4f} R {r:.4f}")


def test_drain_returns_everything():
    print("\nswitching off hands back what is held, unshaped")
    sh = MaskShaper(SR)
    sh.set_params(floor_db=12)
    d = noise(10000, 7)
    w = d.mean(axis=1) * 0.5
    st, out = sh.process(d[:3001], w[:3001])
    held_st, held_w = sh.drain()
    total = len(out) + len(held_w)
    check("emitted + drained == fed", total == 3001, f"{len(out)} + {len(held_w)}")
    check("drained dry continues exactly where emitted dry stopped",
          np.array_equal(np.concatenate([st, held_st]), d[:3001]))
    check("drained wet is the raw input", np.array_equal(held_w, w[len(out):3001]))
    check("nothing pending afterwards", not sh.pending)


def main() -> int:
    print("mask shaper tests (no audio hardware, no models)")
    test_count_aligned()
    test_transparent_where_nothing_to_reshape()
    test_floor()
    test_release_holds_the_gain()
    test_no_burst_from_a_silent_bin()
    test_stereo_wet()
    test_drain_returns_everything()
    print(f"\n{'FAIL' if FAILURES else 'PASS'}"
          + (f" — {len(FAILURES)}: {', '.join(FAILURES)}" if FAILURES else ""))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
