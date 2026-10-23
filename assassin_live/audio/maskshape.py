"""Reshape the chain's own per-bin decision — the "permissiveness" controls
(ROADMAP B4, C12; HANDOVER "The permissiveness knob").

Every model in the registry, and the stereo rebuild, leaves an implied
spectral gain behind: mask[k] = |Wet[k]| / |Dry[k]|. This stage edits that
decision without asking any processor to change, so it works on all of them:

    floor_db    how far any bin may be pulled down at all (0 = no floor).
                Generalises speechdenoiser's atten_lim_db to every model.
    release_ms  how fast a bin's gain may FALL. A gain that collapses faster
                than a syllable is what the corpus tags as vocal-loss and
                pumping (dpdfnet_hr: 36/104 and 38/104).
    attack_ms   how fast it may RISE (0 = instantly, the default: a voice
                coming back should be let through at once).
    exponent    < 1 lets uncertain bins through, > 1 is stricter. Kept for
                the bench; measured as a dead end on dpdfnet_hr and
                content-dependent on gtcrn/dtln, so the panel does not show it.

THE SMOOTHING IS DONE ON POWERS, NOT ON THE RATIO. The first implementation
(tests/bench_quality.py shape_mask, 2026-09-08) smoothed |Wet|/|Dry| itself
and hit an unresolved bug: wherever Dry is near zero in a bin the ratio is
meaningless (clipped to 8x), and a smoothed copy of that reading survived the
release window and amplified a later, perfectly normal frame. Here |Dry|^2 and
|Wet|^2 are smoothed separately with the SAME coefficient, and the gain is
sqrt(P_W / P_D). That ratio is a Dry-energy-weighted average of the per-frame
ratios, so a frame where Dry is near zero contributes almost nothing to either
accumulator and cannot leave a stale value behind; and the gain is clamped at
1, so whatever the accumulators say, no bin can come out louder than the dry
signal it came from. De-musicing never needs a gain above 1.

APPLIED AS A CORRECTION, NOT A RESYNTHESIS. None of the shipped models is a
pure magnitude mask (dpdfnet_hr changes phase, dtln refines in the time
domain), so rebuilding output as `shaped_mask * Dry` throws away part of what
the model did — measured at identity as dSI-SDR +1.07 -> -3.26 on dtln. The
wet signal is scaled toward the target instead, and only the shortfall in
bins the model removed outright is made up from dry.

STREAMING CONTRACT. process(dry_stereo, wet) -> (dry_stereo, wet), equal
lengths in, equal lengths out, and COUNT-ALIGNED: the n-th sample out is the
n-th sample in. The framing's warm-up is discarded rather than emitted as a
priming offset, so the engine's positional dry/wet pairing stays correct when
the stage is switched on mid-stream (it costs N_FFT - HOP of added delay,
5.3 ms, measured into latency like any other). Switching it off returns what
it still holds, unshaped, through drain() — nothing is dropped either way.
`wet` may be mono (n,) or stereo (n, 2); the mask is always taken on the mono
downmix and the same correction is applied to both channels, so the image of
a stereo separator survives.
"""

import numpy as np

from ..processors.stft import sqrt_hann

N_FFT, HOP = 512, 256

# How far the correction may scale wet up toward the target. Past this the dry
# residual takes over, which is bounded by the dry signal itself.
GAIN_CEIL = 10.0
EPS = 1e-9
# Accumulator floor, about -100 dBFS per bin at this framing. Absolute only: a
# floor relative to the frame's mean power biased every quiet bin's gain down
# (measured: 3e-3 error at identity on a stereo separator, whose independent
# channels make many near-cancelling bins in the mono mix), and with the gain
# capped at 1 there is no blow-up for it to prevent.
ABS_FLOOR = 1e-9
# A bin whose dry falls this far below its own running level HOLDS its
# accumulators rather than updating them. A near-silent frame has no decision
# to contribute, and letting it in is not neutral: its residual-over-nothing
# reads as a rising gain, the attack takes it at full weight, and the gain then
# sits wide open for the whole release after the silence ends (found by
# tests/test_mask_shaper.py's hole test). -40 dB.
HOLD_BELOW = 1e-4

_EMPTY_ST = np.zeros((0, 2), dtype=np.float32)


class MaskShaper:
    latency_samples = N_FFT - HOP   # added delay while active, 5.3 ms @ 48 kHz

    def __init__(self, sample_rate: int = 48000):
        self.sample_rate = sample_rate
        self.floor_db = 0.0
        self.attack_ms = 0.0
        self.release_ms = 0.0
        self.exponent = 1.0
        self._win = sqrt_hann(N_FFT)
        norm = np.zeros(HOP, dtype=np.float64)
        for k in range(N_FFT // HOP):
            norm += self._win[k * HOP:(k + 1) * HOP] ** 2
        self._norm = norm.astype(np.float32)
        self.reset()

    # -- parameters ---------------------------------------------------------
    def set_params(self, floor_db: float | None = None,
                   release_ms: float | None = None,
                   attack_ms: float | None = None,
                   exponent: float | None = None) -> None:
        """Plain attribute writes, so the UI thread can call this while the
        worker runs; each hop reads them once."""
        if floor_db is not None:
            self.floor_db = max(0.0, float(floor_db))
        if release_ms is not None:
            self.release_ms = max(0.0, float(release_ms))
        if attack_ms is not None:
            self.attack_ms = max(0.0, float(attack_ms))
        if exponent is not None:
            self.exponent = float(exponent)

    @property
    def identity(self) -> bool:
        """True when every control is at its no-op value. The stage is then
        bypassed outright (no framing, no added delay)."""
        return (self.floor_db < 0.5 and self.release_ms <= 0.0
                and self.attack_ms <= 0.0 and self.exponent == 1.0)

    # -- state --------------------------------------------------------------
    def reset(self) -> None:
        self._in_st = np.zeros((N_FFT, 2), dtype=np.float32)   # dry analysis window
        self._in_w = None            # shaped like wet once the first block arrives
        self._ola = None
        self._p_d = None
        self._p_w = None
        # Framed but not yet emitted (one hop at steady state: the framing's
        # warm-up), and not yet framed. Both are what drain() hands back.
        self._q_st = _EMPTY_ST
        self._q_w = None
        self._fresh_st = _EMPTY_ST
        self._fresh_w = None
        self._primed = False

    @property
    def pending(self) -> bool:
        """Holding samples that drain() should hand back."""
        return self._q_w is not None and (len(self._q_w) + len(self._fresh_w)) > 0

    def drain(self) -> tuple[np.ndarray, np.ndarray]:
        """Everything held, unshaped, and a clean reset — for switching off
        without dropping samples (which would skew the engine's pairing)."""
        if self._q_w is None:
            self.reset()
            return _EMPTY_ST, np.zeros(0, dtype=np.float32)
        st = np.concatenate([self._q_st, self._fresh_st])
        w = np.concatenate([self._q_w, self._fresh_w])
        self.reset()
        return st, w

    # -- processing ---------------------------------------------------------
    def _coeff(self, ms: float) -> float:
        return float(np.exp(-HOP / (self.sample_rate * ms / 1000.0))) if ms > 0 else 0.0

    def process(self, dry_st: np.ndarray, wet: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        n = min(len(dry_st), len(wet))
        dry_st = dry_st[:n].astype(np.float32)
        wet = wet[:n].astype(np.float32)
        stereo = wet.ndim == 2
        if self._in_w is None:
            shape = (N_FFT, 2) if stereo else (N_FFT,)
            self._in_w = np.zeros(shape, dtype=np.float32)
            self._ola = np.zeros(shape, dtype=np.float32)
            empty = np.zeros((0, 2) if stereo else 0, dtype=np.float32)
            self._q_w, self._fresh_w = empty, empty.copy()

        self._fresh_st = np.concatenate([self._fresh_st, dry_st])
        self._fresh_w = np.concatenate([self._fresh_w, wet])

        floor = 10.0 ** (-self.floor_db / 20.0) if self.floor_db >= 0.5 else 0.0
        a_up, a_down = self._coeff(self.attack_ms), self._coeff(self.release_ms)
        exp = self.exponent
        win = self._win[:, None] if stereo else self._win
        norm = self._norm[:, None] if stereo else self._norm

        out_st, out_w = [], []
        while len(self._fresh_w) >= HOP:
            st, self._fresh_st = self._fresh_st[:HOP], self._fresh_st[HOP:]
            w, self._fresh_w = self._fresh_w[:HOP], self._fresh_w[HOP:]
            self._q_st = np.concatenate([self._q_st, st])
            self._q_w = np.concatenate([self._q_w, w])

            self._in_st[:-HOP] = self._in_st[HOP:]
            self._in_st[-HOP:] = st
            self._in_w[:-HOP] = self._in_w[HOP:]
            self._in_w[-HOP:] = w

            spec_dst = np.fft.rfft(self._in_st * self._win[:, None], axis=0)
            spec_d = spec_dst.mean(axis=1)
            spec_w = np.fft.rfft(self._in_w * win, axis=0)
            spec_wm = spec_w.mean(axis=1) if stereo else spec_w

            pow_d = spec_d.real ** 2 + spec_d.imag ** 2
            pow_w = spec_wm.real ** 2 + spec_wm.imag ** 2
            if self._p_d is None:
                self._p_d, self._p_w = pow_d.copy(), pow_w.copy()
            else:
                # Direction per bin from where this frame would pull the gain;
                # the SAME coefficient then goes to both accumulators, which
                # is what keeps their ratio a weighted average of real
                # per-frame ratios rather than a drifting quotient.
                rising = pow_w * (self._p_d + ABS_FLOOR) > self._p_w * (pow_d + ABS_FLOOR)
                c = np.where(rising, a_up, a_down)
                if a_up or a_down:
                    c = np.where(pow_d > HOLD_BELOW * self._p_d, c, 1.0)
                self._p_d = c * self._p_d + (1.0 - c) * pow_d
                self._p_w = c * self._p_w + (1.0 - c) * pow_w
            want = np.minimum(np.sqrt(self._p_w / (self._p_d + ABS_FLOOR)), 1.0)
            if exp != 1.0:
                want = want ** exp
            if floor > 0.0:
                np.maximum(want, floor, out=want)

            # Unclipped: with want <= 1 this makes |Wet * gain| exactly
            # want * |Dry| wherever the gain is not at its ceiling, so no bin
            # can leave louder than its dry. Clipping the mask (8x, as the
            # stereo rebuild does) broke that bound wherever the model's
            # output outweighed its input by more than the clip.
            mask = np.abs(spec_wm) / (np.abs(spec_d) + EPS)
            gain = np.clip(want / (mask + EPS), 0.0, GAIN_CEIL)
            resid = np.maximum(want - mask * gain, 0.0)

            if stereo:
                # Residual from each dry channel, not the mono sum, so a
                # stereo separator keeps its image in made-up bins too.
                frame = np.fft.irfft(spec_w * gain[:, None]
                                     + spec_dst * resid[:, None], n=N_FFT, axis=0)
            else:
                frame = np.fft.irfft(spec_w * gain + spec_d * resid, n=N_FFT)
            self._ola += frame.astype(np.float32) * win
            hop_out = self._ola[:HOP] / norm
            self._ola[:-HOP] = self._ola[HOP:]
            self._ola[-HOP:] = 0.0
            if not self._primed:
                # The first hop out covers the zeros the window started on,
                # not any input. Discarded, so output stays count-aligned.
                self._primed = True
                continue
            out_w.append(hop_out.copy())
            out_st.append(self._q_st[:HOP])
            self._q_st = self._q_st[HOP:]
            self._q_w = self._q_w[HOP:]

        if not out_w:
            return _EMPTY_ST, self._q_w[:0]
        return np.concatenate(out_st), np.concatenate(out_w)
