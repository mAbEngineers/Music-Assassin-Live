"""MRX (MERL cocktail-fork separation) as a StreamProcessor -- the first
three-way dialogue/music/effects model in this registry, and the only route
to "keep sound effects" in the live path.

WHY A THREE-WAY MODEL AND NOT A KNOB ON THE EXISTING ONES

Every other processor here emits one scalar per bin saying how speech-like
that bin is. Exponents, floors and release times are monotonic functions of
that scalar: they change how hard the ranking is applied but not the ranking
itself, so no setting of them can keep a door slam while dropping a snare --
the two sit next to each other in the single ordering the model gives.
MRX returns a separate effects mask, which is a second axis of evidence
rather than a reshuffle of the first. See docs/IDEAS.md section 1.1 and
ROADMAP section 11's 2026-10-23 scope note.

WHAT IT COSTS, AND WHY THE SWITCH IS NOT FREE

MRX is a bidirectional LSTM. It reads a whole chunk of frames before it
decides anything, so unlike StemgenRT this cannot be made block-synchronous
at any price -- the delay is the model, not the implementation. The chunk is
therefore a constructor parameter and part of the config name
(`mrx_1000ms`), exactly as it is for Spleeter (ROADMAP A1), because it is
the dominant latency term and the thing worth sweeping.

Per window the processor runs three STFTs (1024/2048/8192, hop 256), one
session, and one iSTFT per emitted stem per resolution. Windows overlap by
`edge` samples at each end, which are computed and thrown away: a chunk
boundary falls mid-syllable otherwise and the masks either side of it
disagree. So cost scales as chunk/advance, and advance = chunk - 2*edge.

LOUDNESS

The offline repo normalises a whole file to -27 LUFS before calling MRX
(`fast_separation._loudness_gain`) because the int8 graph was calibrated at
DnR level -- its own metadata says "budget 40 dB". There is no whole file
here, so this keeps a slow running estimate instead and undoes the gain on
the way out. The time constant is deliberately long: a fast one would make
the separation depend on what happened a moment ago, which is the trap
ROADMAP section 2.1 spent a week inside. This is a stopgap for B10, not a
replacement for it.

WHAT THE EFFECTS STEM IS ACTUALLY LIKE, MEASURED

MRX is trained on DnR: film speech, score and foley. Music with SUNG vocals
over a band mix is out of distribution for it, and it shows. On four
male-lead clips from the b3 corpus -- material with no sound effects in it
at all, so every bit of the effects stem is leakage -- the raw effects stem
came out at -6.2 dB relative to the dialogue stem, and over-subtraction
(below) brought that to -10.6 dB. On the worst clip it is still -0.7 dB:
parity with the dialogue. The offline repo measures -21.3 dB on cinematic
content, which is the material this model is for and where the maintainer
preferred it by ear.

So: keep this switch on for film and TV, and expect it to add music back on
music with singing. That is a property of the model, not of this wrapper.
"""

import numpy as np

from . import cstft
from .base import StreamProcessor
from .ort_util import make_session

WINDOWS = (1024, 2048, 8192)
HOP = 256

# mask stem axis, matching fast_separation._mrx_chunk's `order`
MUSIC, SPEECH, SFX = 0, 1, 2

TARGET_LUFS = -27.0
# RMS-to-LUFS offset for an unweighted estimate, as used by the offline
# repo's fallback when pyloudnorm is missing.
LUFS_OFFSET = -0.691
# Gain is clamped so a near-silent passage cannot ask for +80 dB and hand the
# model a wall of amplified noise.
GAIN_MIN_DB, GAIN_MAX_DB = -20.0, 40.0
LOUDNESS_TAU_S = 4.0

# Over-subtraction of the music stem from the effects stem, which the offline
# repo applies BY DEFAULT for MRX (`fast_separation.clean_effects`) and which
# is the difference between -16.4 dB and -21.3 dB of music leakage into
# effects there. Done here in the model's own three STFT domains rather than
# in a separate 2048/512 analysis, so it costs two multiplies per bin and no
# extra transform. Defaults match the offline call's strength=1.0, floor=0.05.
CLEAN_BETA = 1.0
CLEAN_FLOOR = 0.05


class MrxProcessor(StreamProcessor):
    name = "mrx"
    sample_rate = 44100
    wants_stereo = False          # MRX takes a mono mixture

    # Defaults match the registry's _MRX_DEFAULT_MS and MRX_EDGE_FRAC, so
    # constructing this directly gives the same thing create("mrx") does.
    def __init__(self, model_path: str, chunk_ms: float = 2000.0,
                 edge_ms: float = 200.0, keep_effects: bool = True,
                 clean_beta: float = CLEAN_BETA,
                 clean_floor: float = CLEAN_FLOOR):
        self.session = make_session(model_path)
        outs = {o.name for o in self.session.get_outputs()}
        missing = [f"mask_{w}" for w in WINDOWS if f"mask_{w}" not in outs]
        if missing:
            raise ValueError(f"{model_path}: no {missing} (not an MRX graph?)")

        # Both must be whole numbers of hops or the three resolutions stop
        # agreeing on T, which is what lets their outputs be summed.
        self._chunk = max(2, _hops(chunk_ms, self.sample_rate)) * HOP
        self._edge = _hops(edge_ms, self.sample_rate) * HOP
        if self._edge * 2 >= self._chunk:
            raise ValueError(f"edge_ms {edge_ms} too large for chunk_ms {chunk_ms}")
        self._advance = self._chunk - 2 * self._edge

        self._win = {w: cstft.hann_periodic(w) for w in WINDOWS}
        self._keep_effects = bool(keep_effects)
        self._clean_beta = float(clean_beta)
        self._clean_floor = float(clean_floor)
        self._alpha = float(np.exp(-self._advance / (LOUDNESS_TAU_S * self.sample_rate)))
        self.reset()

    # -- the contract ----------------------------------------------------

    @property
    def latency_samples(self) -> int:
        """Worst-case lag: `edge` samples of lookahead, plus up to one whole
        advance spent waiting for the window to fill. The engine sizes its
        FIFO from this, so it is the bound and not the average (which is
        about `advance/2 + edge` lower)."""
        return self._advance + self._edge

    def reset(self) -> None:
        # _buf holds input from absolute sample _buf_start. It starts at
        # -edge, pre-filled with silence, so window 0 has its left margin.
        self._buf = np.zeros(self._edge, dtype=np.float32)
        self._buf_start = -self._edge
        self._out_next = 0
        self._mean_sq = 0.0
        self._primed = False

    def feed(self, x: np.ndarray) -> np.ndarray:
        self._buf = np.concatenate([self._buf, np.asarray(x, dtype=np.float32)])
        out = []
        # A window needs input up to _out_next + advance + edge.
        while self._buf_start + len(self._buf) >= self._out_next + self._advance + self._edge:
            lo = self._out_next - self._edge - self._buf_start
            seg = self._buf[lo:lo + self._chunk]
            out.append(self._run(seg))
            self._out_next += self._advance
            keep = self._out_next - self._edge - self._buf_start
            self._buf = self._buf[keep:]
            self._buf_start += keep
        if not out:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(out)

    # -- optional setters the engine duck-types --------------------------

    def set_keep_effects(self, enabled: bool) -> None:
        """Add MRX's effects stem back alongside dialogue.

        Off, this processor keeps dialogue only and behaves like every other
        model in the registry. On, it returns dialogue + effects, which is
        what makes footsteps and door slams survive music removal."""
        self._keep_effects = bool(enabled)

    @property
    def keeps_effects(self) -> bool:
        """Marks this processor as able to honour set_keep_effects, so the UI
        can enable its switch for MRX and explain itself for the rest."""
        return True

    # -- internals -------------------------------------------------------

    def _run(self, seg: np.ndarray) -> np.ndarray:
        g = self._loudness_gain(seg)
        sig = seg * g

        specs, feeds = {}, {}
        for w in WINDOWS:
            s = cstft.stft(sig, w, HOP, self._win[w])
            specs[w] = s
            feeds[f"mag_{w}"] = np.abs(s)[None]          # [1, F, T]

        got = self.session.run([f"mask_{w}" for w in WINDOWS], feeds)
        masks = dict(zip(WINDOWS, got))
        stems = (SPEECH, SFX) if self._keep_effects else (SPEECH,)

        y = np.zeros(self._chunk, dtype=np.float32)
        for w in WINDOWS:
            s, m = specs[w], masks[w]
            for k in stems:
                spec = m[0, k] * s
                if k == SFX and self._clean_beta > 0.0:
                    spec = spec * self._clean_gain(m[0, MUSIC], m[0, k], s)
                y += cstft.istft(spec, w, HOP, self._win[w], self._chunk)
        # Undo the analysis gain so the engine's dry/wet mix stays level-matched.
        y /= g
        return y[self._edge:self._edge + self._advance]

    def _clean_gain(self, music_mask: np.ndarray, sfx_mask: np.ndarray,
                    spec: np.ndarray) -> np.ndarray:
        """Per-bin over-subtraction gain for the effects stem.

        `max(floor, 1 - beta * music/effects)` on magnitudes, using the
        model's own music estimate -- so a bin the model thinks is mostly
        music is held down even where it also assigned it some effects
        energy. The masks share `spec`, so its magnitude cancels and only
        the mask ratio matters; it is passed in anyway because a future
        magnitude-weighted variant would need it.
        """
        ratio = music_mask / (sfx_mask + 1e-9)
        return np.maximum(self._clean_floor,
                          1.0 - self._clean_beta * ratio).astype(np.float32)

    def _loudness_gain(self, seg: np.ndarray) -> float:
        """Slow running gain toward TARGET_LUFS. The first window seeds the
        estimate outright rather than easing into it from zero, which would
        feed the model a wildly wrong level for the first few seconds."""
        ms = float(np.mean(np.square(seg, dtype=np.float64))) if len(seg) else 0.0
        if not self._primed:
            self._mean_sq, self._primed = ms, True
        else:
            self._mean_sq = self._alpha * self._mean_sq + (1.0 - self._alpha) * ms
        if self._mean_sq <= 0.0:
            return 1.0
        lufs = 10.0 * np.log10(self._mean_sq) + LUFS_OFFSET
        db = float(np.clip(TARGET_LUFS - lufs, GAIN_MIN_DB, GAIN_MAX_DB))
        return float(10.0 ** (db / 20.0))


def _hops(ms: float, sr: int) -> int:
    return max(1, int(round(ms * sr / 1000.0 / HOP)))
