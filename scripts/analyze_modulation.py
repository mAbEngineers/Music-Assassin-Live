#!/usr/bin/env python3
"""Does a model's voice damage follow the voice's pitch behaviour?

The hypothesis (2026-10-24): dpdfnet_hr, a speech enhancer, chews voices whose
pitch swings a lot, and a separator trained on music vocals does not. The
evidence that matters is `vocal_ret_db`, which the bench measures on the
isolated vocals with no music present. So the damage cannot come from the mix;
the question is which property of the voice drives it.

    python scripts/analyze_modulation.py --corpus CORPUS RESULTS_DIR [RESULTS_DIR ...]

RESULTS_DIR holds the results_*.jsonl that scripts/bench_subset.py wrote. For
each clip the pitch is tracked on its reference vocals.wav (a plain normalised
autocorrelation tracker, adequate for ranking clips, not for transcription)
and these are reported next to each model's vocal_ret_db:

  f0_median  Hz                       is it high-pitched
  range_st   semitones, p95 - p5      how far the pitch travels
  speed_st_s semitones/s, mean |d|    how fast it moves
  voiced     fraction of frames

then a Spearman rank correlation per model. With about twenty clips a
correlation under ~0.45 is noise; read it as a lead, not a result.
"""

import argparse
import json
import sys
import warnings
import wave
from pathlib import Path

import numpy as np

SR = 48000
FRAME, HOP = 1920, 480            # 40 ms window, 10 ms hop
LAG_MIN, LAG_MAX = SR // 900, SR // 70


def read_mono(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        sw, ch = w.getsampwidth(), w.getnchannels()
        raw = w.readframes(w.getnframes())
        if w.getframerate() != SR:
            raise SystemExit(f"{path}: expected {SR} Hz")
    if sw == 2:
        x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    elif sw == 4:
        x = np.frombuffer(raw, np.int32).astype(np.float32) / 2147483648.0
    else:
        raise SystemExit(f"{path}: unsupported sample width {sw}")
    return x.reshape(-1, ch).mean(axis=1) if ch > 1 else x


def track_pitch(x: np.ndarray) -> np.ndarray:
    """Hz per frame, NaN where unvoiced."""
    n = 1 + (len(x) - FRAME) // HOP
    idx = np.arange(FRAME)[None, :] + HOP * np.arange(n)[:, None]
    fr = x[idx] * np.hanning(FRAME).astype(np.float32)
    rms = np.sqrt(np.mean(fr ** 2, axis=1))
    gate = rms > 10 ** (-45 / 20) * rms.max()
    spec = np.fft.rfft(fr, n=2 * FRAME, axis=1)
    ac = np.fft.irfft(np.abs(spec) ** 2, axis=1)[:, :LAG_MAX + 1]
    ac = ac / (ac[:, :1] + 1e-12)
    f0 = np.full(n, np.nan)
    for i in np.flatnonzero(gate):
        seg = ac[i, LAG_MIN:LAG_MAX]
        k = int(np.argmax(seg))
        if seg[k] > 0.55:
            lag = float(LAG_MIN + k)
            if 0 < k < len(seg) - 1:      # parabolic peak: the integer lag is too coarse for speed
                a, b, c = seg[k - 1], seg[k], seg[k + 1]
                lag += 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
            f0[i] = SR / lag
    return f0


def features(f0: np.ndarray) -> dict:
    voiced = ~np.isnan(f0)
    st = np.full_like(f0, np.nan)
    st[voiced] = 12 * np.log2(f0[voiced] / 55.0)
    # 5-frame median to knock out single-frame octave errors
    pad = np.pad(st, 2, constant_values=np.nan)
    med = np.nanmedian(np.stack([pad[i:i + len(st)] for i in range(5)]), axis=0)
    v = ~np.isnan(med)
    if v.sum() < 50:
        return dict(f0_median=np.nan, range_st=np.nan, speed_st_s=np.nan, voiced=v.mean())
    d = np.abs(np.diff(med)) / (HOP / SR)
    ok = ~np.isnan(d) & (d < 3.0 / (HOP / SR))      # drop remaining octave jumps
    return dict(f0_median=float(np.median(f0[voiced])),
                range_st=float(np.percentile(med[v], 95) - np.percentile(med[v], 5)),
                speed_st_s=float(np.mean(d[ok])) if ok.any() else np.nan,
                voiced=float(v.mean()))


def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 5:
        return float("nan")
    ra, rb = (np.argsort(np.argsort(v[m])).astype(float) for v in (a, b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> int:
    warnings.filterwarnings("ignore", message="All-NaN")
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, required=True)
    ap.add_argument("results", type=Path, nargs="+")
    args = ap.parse_args()
    rows = [json.loads(line) for d in args.results for p in sorted(d.glob("results_*.jsonl"))
            for line in p.read_text().splitlines()]
    rows = [r for r in rows if "error" not in r]
    clips = sorted({r["clip"] for r in rows})
    models = sorted({r["model"] for r in rows})
    table = []
    for c in clips:
        feat = features(track_pitch(read_mono(args.corpus / c / "vocals.wav")))
        ret = {m: np.mean([r["vocal_ret_db"] for r in rows
                           if r["clip"] == c and r["model"] == m and "vocal_ret_db" in r] or [np.nan])
               for m in models}
        table.append((c, feat, ret))
    print(f"{'clip':44s} {'f0 Hz':>6s} {'range':>6s} {'speed':>6s} {'voiced':>6s} "
          + " ".join(f"{m[:10]:>10s}" for m in models))
    for c, f, ret in sorted(table, key=lambda t: t[2][models[0]]):
        print(f"{c[:44]:44s} {f['f0_median']:6.0f} {f['range_st']:6.1f} {f['speed_st_s']:6.1f} "
              f"{f['voiced']:6.2f} " + " ".join(f"{ret[m]:10.2f}" for m in models))
    print("\nSpearman rank correlation of vocal_ret_db with each property "
          "(negative = more of it, more damage)")
    for key in ("f0_median", "range_st", "speed_st_s", "voiced"):
        print(f"  {key:11s} " + "  ".join(
            f"{m}: {spearman([t[1][key] for t in table], [t[2][m] for t in table]):+.2f}"
            for m in models))
    print(f"  (n = {len(table)} clips)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
