#!/usr/bin/env python3
"""Turn one dumped clip into a video: every stage's waveform and mel
spectrogram, one playhead, and the audio of each stage as its own track.

    python scripts/visualize_stages.py DUMP_DIR CLIP_STEM [--out FILE.mp4]
    python scripts/visualize_stages.py DUMP_DIR --all        # every clip found

DUMP_DIR is what `tests/bench_quality.py --dump-audio` or
`scripts/bench_subset.py --dump-audio` wrote; CLIP_STEM is the filename up to
the stage name, e.g. `male_lead__Colors_FLOW_CodeGeassOP1_a100` (from
`..._a100_stemgen.wav`).

"oracle" is the bench's ideal-ratio-mask output: the mix with a perfect
per-bin mask applied (tests/bench_quality.py oracle_irm). It is the best a
mask can do, not the raw vocals, so it still carries some music, and "red vs
oracle" below means more than a perfect mask would have kept. "ceiling" is the
offline separator.

Rows, top to bottom (same colour scale on every spectrogram, so brightness is
comparable between rows):
  * input / each model / ceiling / oracle: waveform and mel spectrogram
  * "vs oracle": the model's mel spectrogram minus the oracle's, in dB. Red =
    the model is louder than the true vocals there (music it kept, artefacts it
    added: narrow horizontal red lines are tonal music, isolated red dots are
    the "metallic" noise). Blue = it is quieter (voice it cut: vertical blue
    bands are pumping, blue along a harmonic is a chewed note). A waveform
    subtraction is deliberately not used: separators change phase, and the
    difference would just reproduce the voice
  * level over time, per stage, in dB: where a model's line falls under the
    oracle's, it is cutting voice; where it sits above in a gap, it is
    leaking music

Playback: the mp4 carries one audio track per stage. In mpv press `#` to
cycle tracks; VLC has Audio > Audio Track. The first track is the input.

Needs matplotlib and ffmpeg. The app's own venv has no matplotlib; use
~/.local/share/music-assassin/tools-venv (python3 -m venv, pip install
matplotlib numpy).
"""

import argparse
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
N_FFT, HOP, N_MELS = 2048, 512, 128
F_MIN, F_MAX = 30.0, 20000.0
DB_RANGE = 80.0
DIFF_RANGE = 25.0

# Order and display names. A stage is drawn only if its file exists.
STAGES = ("input", "stemgen", "dpdfnet_hr", "gtcrn", "dtln", "ceiling", "oracle")
MODELS = ("stemgen", "dpdfnet_hr", "gtcrn", "dtln")
COLORS = {"input": "#8a8f98", "stemgen": "#2f9e44", "dpdfnet_hr": "#d9480f",
          "gtcrn": "#7048e8", "dtln": "#1c7ed6", "ceiling": "#868e96", "oracle": "#000000"}


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit(f"{path}: expected 16-bit PCM")
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        if w.getnchannels() > 1:
            x = x.reshape(-1, w.getnchannels()).mean(axis=1)
    return x.astype(np.float32) / 32768.0


def mel_filterbank() -> np.ndarray:
    def hz2mel(f): return 2595.0 * np.log10(1.0 + f / 700.0)
    def mel2hz(m): return 700.0 * (10.0 ** (m / 2595.0) - 1.0)
    pts = mel2hz(np.linspace(hz2mel(F_MIN), hz2mel(F_MAX), N_MELS + 2))
    freqs = np.fft.rfftfreq(N_FFT, 1.0 / SR)
    fb = np.zeros((N_MELS, len(freqs)), dtype=np.float32)
    for i in range(N_MELS):
        lo, mid, hi = pts[i], pts[i + 1], pts[i + 2]
        up = (freqs - lo) / (mid - lo)
        down = (hi - freqs) / (hi - mid)
        fb[i] = np.maximum(0.0, np.minimum(up, down))
    return fb / np.maximum(fb.sum(axis=1, keepdims=True), 1e-9) * 2.0


def mel_db(x: np.ndarray, fb: np.ndarray) -> np.ndarray:
    win = np.hanning(N_FFT).astype(np.float32)
    n = 1 + max(0, (len(x) - N_FFT) // HOP)
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(n)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
    return 10.0 * np.log10(fb @ spec.T + 1e-12)          # (mels, frames)


def level_db(x: np.ndarray, win_s: float = 0.2) -> tuple[np.ndarray, np.ndarray]:
    w = int(win_s * SR)
    n = len(x) // w
    rms = np.sqrt(np.mean(x[:n * w].reshape(n, w) ** 2, axis=1) + 1e-12)
    return (np.arange(n) + 0.5) * win_s, 20.0 * np.log10(rms)


def render_figure(stage_audio: dict[str, np.ndarray], title: str, png: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import gridspec

    fb = mel_filterbank()
    names = list(stage_audio)
    oracle = stage_audio.get("oracle")
    leaks = [m for m in MODELS if m in stage_audio and oracle is not None]
    n = min(len(v) for v in stage_audio.values())
    dur = n / SR

    mels = {k: mel_db(v[:n], fb) for k, v in stage_audio.items()}
    ref = np.percentile(mels["input"], 99.5)
    vmin, vmax = ref - DB_RANGE, ref
    # Magnitude difference, not waveform difference. Bins where both are far
    # below the picture's floor carry no information and are left neutral.
    diffs = {}
    for m in leaks:
        d = mels[m] - mels["oracle"]
        quiet = np.maximum(mels[m], mels["oracle"]) < vmin + 15.0
        diffs[m] = np.where(quiet, 0.0, np.clip(d, -DIFF_RANGE, DIFF_RANGE))

    h = 2 * len(names) + len(leaks) * 1.6 + 2.2          # inches
    W, H = 1600, int(max(900, h * 78) // 2 * 2)
    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
    spec = gridspec.GridSpec(
        2 * len(names) + len(leaks) + 1, 1, figure=fig,
        height_ratios=[0.55, 1.6] * len(names) + [1.6] * len(leaks) + [2.2],
        left=0.1, right=0.915, top=0.955, bottom=0.04, hspace=0.35)
    fig.suptitle(title, fontsize=13, x=0.1, ha="left", y=0.985)
    extent = [0, dur, 0, N_MELS]
    ticks_hz = [100, 300, 1000, 3000, 10000]
    hz2mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
    m0, m1 = hz2mel(F_MIN), hz2mel(F_MAX)
    tick_pos = [(hz2mel(f) - m0) / (m1 - m0) * N_MELS for f in ticks_hz]

    def style(ax, ylabel):
        ax.set_ylabel(ylabel, fontsize=9, rotation=0, ha="right", va="center", labelpad=6)
        ax.tick_params(labelsize=7)
        ax.set_xlim(0, dur)

    row, last_im = 0, None
    t = np.arange(n) / SR
    for k in names:
        v = stage_audio[k][:n]
        ax = fig.add_subplot(spec[row]); row += 1
        step = max(1, n // 4000)
        seg = v[:n // step * step].reshape(-1, step)
        ax.fill_between(t[::step][:len(seg)], seg.min(axis=1), seg.max(axis=1),
                        color=COLORS.get(k, "#444"), linewidth=0)
        ax.set_ylim(-1, 1)
        ax.set_yticks([])
        ax.set_xticklabels([])
        style(ax, k)
        ax = fig.add_subplot(spec[row]); row += 1
        last_im = ax.imshow(mels[k], origin="lower", aspect="auto", extent=extent,
                            vmin=vmin, vmax=vmax, cmap="magma", interpolation="nearest")
        ax.set_yticks(tick_pos); ax.set_yticklabels([f"{f // 1000}k" if f >= 1000 else str(f) for f in ticks_hz])
        ax.set_xticklabels([])
        style(ax, "")
    diff_axes, diff_im = [], None
    for m in leaks:
        ax = fig.add_subplot(spec[row]); row += 1
        diff_im = ax.imshow(diffs[m], origin="lower", aspect="auto", extent=extent,
                            vmin=-DIFF_RANGE, vmax=DIFF_RANGE, cmap="RdBu_r",
                            interpolation="nearest")
        ax.set_yticks(tick_pos); ax.set_yticklabels([f"{f // 1000}k" if f >= 1000 else str(f) for f in ticks_hz])
        ax.set_xticklabels([])
        style(ax, f"{m}\nvs oracle")
        diff_axes.append(ax)
    ax = fig.add_subplot(spec[row])
    for k in names:
        lt, lv = level_db(stage_audio[k][:n])
        ax.plot(lt, lv, color=COLORS.get(k, "#444"), linewidth=2.2 if k == "oracle" else 1.4,
                alpha=1.0 if k in ("oracle", "stemgen", "dpdfnet_hr") else 0.55, label=k)
    ax.set_ylim(-70, 0)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7, ncol=len(names), loc="lower left", frameon=False)
    ax.set_xlabel("seconds", fontsize=8)
    style(ax, "level\n(dB)")

    top, bot = fig.axes[1].get_position(), fig.axes[2 * len(names) - 1].get_position()
    cax = fig.add_axes([0.925, bot.y0, 0.008, top.y1 - bot.y0])
    cb = fig.colorbar(last_im, cax=cax)
    cb.set_label("dB (shared scale)", fontsize=8)
    cb.ax.tick_params(labelsize=7)
    if diff_axes:
        top, bot = diff_axes[0].get_position(), diff_axes[-1].get_position()
        cax2 = fig.add_axes([0.925, bot.y0, 0.008, top.y1 - bot.y0])
        cb2 = fig.colorbar(diff_im, cax=cax2)
        cb2.set_label("model − oracle, dB", fontsize=8)
        cb2.ax.tick_params(labelsize=7)
    fig.savefig(png, dpi=100)
    # Playhead extent in pixels: every panel shares its x axis and margins.
    x0, x1 = 0.1 * W, 0.915 * W
    plt.close(fig)
    return W, H, x0, x1, dur


def make_video(dump: Path, stem: str, out: Path) -> None:
    stages = {s: dump / f"{stem}_{s}.wav" for s in STAGES}
    stages = {s: p for s, p in stages.items() if p.exists()}
    if "input" not in stages or len(stages) < 2:
        raise SystemExit(f"{stem}: need an input file and at least one stage in {dump}")
    audio = {s: read_wav(p) for s, p in stages.items()}
    png = out.with_suffix(".png")
    W, H, x0, x1, dur = render_figure(audio, stem.replace("__", "  /  "), png)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-loop", "1", "-framerate", "12", "-i", str(png)]
    for p in stages.values():
        cmd += ["-i", str(p)]
    cmd += ["-map", "0:v"] + sum((["-map", f"{i + 1}:a"] for i in range(len(stages))), [])
    for i, s in enumerate(stages):
        cmd += [f"-metadata:s:a:{i}", f"title={s}", f"-metadata:s:a:{i}", "language=eng"]
    cmd += ["-vf", f"drawbox=x='{x0:.1f}+({x1 - x0:.1f})*t/{dur:.3f}':y=0:w=2:h=ih:color=cyan@0.9:t=fill",
            "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-disposition:a:0", "default",
            str(out)]
    subprocess.run(cmd, check=True)
    print(f"{out}  ({', '.join(stages)})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", type=Path)
    ap.add_argument("stem", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", type=Path, default=None, help="file, or directory with --all")
    args = ap.parse_args()
    if args.all:
        out_dir = args.out or args.dump / "video"
        out_dir.mkdir(parents=True, exist_ok=True)
        stems = sorted({p.name[:-len("_input.wav")] for p in args.dump.glob("*_input.wav")})
        for s in stems:
            make_video(args.dump, s, out_dir / f"{s}.mp4")
        return 0
    if not args.stem:
        ap.error("give CLIP_STEM or --all")
    out = args.out or args.dump / f"{args.stem}.mp4"
    make_video(args.dump, args.stem, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
