#!/usr/bin/env python3
"""processors/cstft.py against torch — no audio devices, no ONNX models.

cstft exists to let MRX run without torch. MRX's weights are int8, so the
forward transform's SCALE is not free: torch.stft(normalized=True) divides by
sqrt(n_fft), and getting that wrong hands a quantised graph magnitudes it was
never calibrated for. A reimplementation that is merely close in shape would
pass a round-trip test and still feed the model the wrong thing.

So the real check here compares against torch itself, in the float32
configuration fast_separation._mrx_chunk actually runs. torch is NOT an app
dependency, so that half reports NOTHING TESTED when it is missing rather
than passing vacuously — same rule as test_processors_offline.py. The
round-trip and shape checks are pure numpy and always run.
"""

import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.processors import cstft  # noqa: E402
from assassin_live.processors.mrx import HOP, WINDOWS  # noqa: E402

LENGTHS = (44100, 11025, 4410)


def _clip(n, seed=7):
    return (np.random.default_rng(seed).standard_normal(n) * 0.1).astype(np.float32)


def test_shapes():
    """T must come out the same for all three windows, or their masked
    outputs cannot be summed — which is the whole of MRX's design."""
    for n in LENGTHS:
        x = _clip(n)
        ts = set()
        for w in WINDOWS:
            s = cstft.stft(x, w, HOP, cstft.hann_periodic(w))
            assert s.shape[0] == w // 2 + 1, f"w={w}: F={s.shape[0]}"
            assert s.dtype == np.complex64, f"w={w}: {s.dtype}"
            ts.add(s.shape[1])
        assert ts == {1 + n // HOP}, f"n={n}: frame counts disagree: {ts}"
    print(f"  OK  shapes: F = n_fft//2+1, T = 1 + n//{HOP}, equal across {WINDOWS}")


def test_round_trip():
    for n in LENGTHS:
        x = _clip(n)
        for w in WINDOWS:
            win = cstft.hann_periodic(w)
            y = cstft.istft(cstft.stft(x, w, HOP, win), w, HOP, win, n)
            assert len(y) == n, f"w={w}: {len(y)} != {n}"
            err = np.abs(y - x).max() / np.abs(x).max()
            assert err < 1e-5, f"w={w} n={n}: round trip off by {err:.2e}"
    print("  OK  round trip reconstructs the input to under 1e-5 relative")


def test_window_is_periodic_hann():
    """A symmetric hann would be a plausible-looking off-by-one that shifts
    every magnitude slightly. Pin the periodic form explicitly."""
    w = cstft.hann_periodic(8)
    want = np.array([0.0, 0.14644661, 0.5, 0.85355339, 1.0,
                     0.85355339, 0.5, 0.14644661])
    assert np.allclose(w, want, atol=1e-8), f"not periodic hann: {w}"
    assert w[0] == 0.0 and w[-1] != 0.0, "symmetric hann, not periodic"
    print("  OK  hann_periodic matches torch.hann_window(periodic=True)")


def test_against_torch():
    import torch
    worst = {"mag": 0.0, "cpx": 0.0, "istft": 0.0}
    for n in LENGTHS:
        x = _clip(n)
        for w in WINDOWS:
            win = cstft.hann_periodic(w)
            mine = cstft.stft(x, w, HOP, win)
            myy = cstft.istft(mine, w, HOP, win, n)
            # float32, as the offline repo runs it
            wt = torch.hann_window(w)
            ref = torch.stft(torch.from_numpy(x)[None], w, HOP, window=wt,
                             win_length=w, normalized=True, center=True,
                             return_complex=True, pad_mode="reflect")
            refy = torch.istft(ref, w, HOP, window=wt, win_length=w,
                               normalized=True, center=True, length=n)
            rs = ref[0].numpy()
            assert rs.shape == mine.shape, f"w={w} n={n}: {rs.shape} vs {mine.shape}"
            scale = max(float(np.abs(rs).max()), 1e-12)
            worst["mag"] = max(worst["mag"],
                               float(np.abs(np.abs(rs) - np.abs(mine)).max()) / scale)
            worst["cpx"] = max(worst["cpx"], float(np.abs(rs - mine).max()) / scale)
            ry = refy[0].numpy()
            worst["istft"] = max(worst["istft"], float(np.abs(ry - myy).max())
                                 / max(float(np.abs(ry).max()), 1e-12))
    for k, v in worst.items():
        assert v < 1e-5, f"{k} differs from torch by {v:.2e}"
    print(f"  OK  matches torch float32: magnitude {worst['mag']:.1e}, "
          f"complex {worst['cpx']:.1e}, iSTFT {worst['istft']:.1e} "
          f"(float32 epsilon is ~1e-7)")


def main() -> int:
    print("cstft, pure numpy:")
    test_window_is_periodic_hann()
    test_shapes()
    test_round_trip()

    print("\ncstft vs torch:")
    try:
        import torch  # noqa: F401
    except ImportError:
        print("  NOTHING TESTED — torch not importable, so the scale that "
              "MRX's int8 graph depends on is unverified here.")
        print("  torch is not an app dependency; run this on a venv that has "
              "it. Set ALLOW_NO_TORCH=1 to accept that.")
        return 0 if os.environ.get("ALLOW_NO_TORCH") == "1" else 1
    test_against_torch()
    print("\nall passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
