"""Chunk (non-streaming) STFT/iSTFT matching torch.stft/torch.istft exactly.

MRX (processors/mrx.py) is a non-causal model that consumes a whole chunk of
frames at three window sizes at once, so the streaming WOLA in stft.py --
strictly one frame in, one frame out -- cannot carry it. These two functions
are the torch-free equivalents of the offline repo's calls in
`Music-Assassin/fast_separation.py:_mrx_chunk`:

    torch.stft(sig, w, 256, window=torch.hann_window(w), win_length=w,
               normalized=True, center=True, return_complex=True,
               pad_mode="reflect")
    torch.istft(spec, w, 256, window=torch.hann_window(w), win_length=w,
                normalized=True, center=True, length=n)

MRX's weights are int8, so its input scale is not free: `normalized=True`
divides the forward transform by sqrt(n_fft), and getting that wrong feeds
the quantised graph magnitudes it was never calibrated for. Hence these match
torch bit-for-bit rather than approximately -- tests/test_cstft.py checks them
against torch itself when it is importable.
"""

import numpy as np


def hann_periodic(n_fft: int) -> np.ndarray:
    """torch.hann_window(n_fft) -- periodic, which is torch's default."""
    return (0.5 - 0.5 * np.cos(2.0 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float64)


def stft(x: np.ndarray, n_fft: int, hop: int, window: np.ndarray) -> np.ndarray:
    """Mono float32 (n,) -> complex64 [F, T], F = n_fft//2+1, T = 1 + n//hop."""
    pad = n_fft // 2
    xp = np.pad(x.astype(np.float64), (pad, pad), mode="reflect")
    n_frames = 1 + len(x) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n_frames)[:, None]
    frames = xp[idx] * window
    spec = np.fft.rfft(frames, n=n_fft, axis=-1) / np.sqrt(n_fft)
    return np.ascontiguousarray(spec.T).astype(np.complex64)


def istft(spec: np.ndarray, n_fft: int, hop: int, window: np.ndarray,
          length: int) -> np.ndarray:
    """complex [F, T] -> mono float32 (length,). Inverse of stft()."""
    frames = np.fft.irfft(np.asarray(spec).T.astype(np.complex128),
                          n=n_fft, axis=-1) * np.sqrt(n_fft)
    frames = frames * window
    n_frames = frames.shape[0]
    total = n_fft + hop * (n_frames - 1)
    y = np.zeros(total, dtype=np.float64)
    env = np.zeros(total, dtype=np.float64)
    w2 = window ** 2
    for t in range(n_frames):
        s = t * hop
        y[s:s + n_fft] += frames[t]
        env[s:s + n_fft] += w2
    pad = n_fft // 2
    y, env = y[pad:pad + length], env[pad:pad + length]
    # torch zeroes samples whose window envelope is below this, rather than
    # dividing by it; NOLA holds for hann at hop <= n_fft/2 so it never bites
    # in the middle, only conceivably at the very edges.
    out = np.where(env > 1e-11, y / np.where(env > 1e-11, env, 1.0), 0.0)
    return out.astype(np.float32)
