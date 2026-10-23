"""StemgenRT vocals as a StreamProcessor — the first causal music separator
with public weights (docs/RESEARCH_PRIOR_ART.md §1.2).

sweetspotsoundsystem/stemgen-rt v0.7.0 (MIT) ships one ONNX file holding
three independent causal separators (drums, bass, vocals), descended from
lucidrains' HS-TasNet. scripts/fetch_stemgen.py downloads it, verifies the
published SHA-256, and cuts out the vocals sub-model alone (11 MB, 2.72M
parameters, bit-identical output, ~1/3 of the full graph's cost). This class
runs that extract; it also accepts the full graph, picking the vocals source
out of `separated_chunk`.

WHAT THE MODEL'S OWN METADATA SAYS, and what this class does about it:

  * stereo 44.1 kHz in, 128-sample hops, recurrent state tensors carried
    between calls, `initial_state: all_zeros` -> wants_stereo, sample_rate
    44100, a hop FIFO under whatever block size the engine feeds, zeros on
    reset(). The engine's soxr pair does 48k <-> 44.1k.
  * `output_alignment: previous_input_hop`, `preroll: one_invalid_hop` ->
    each call returns the PREVIOUS hop, and the first return is junk. It is
    discarded, so from then on the n-th emitted sample is the n-th input
    sample: count-aligned with no priming offset, which is what the engine's
    positional dry/wet pairing assumes for a wants_stereo processor (no lag
    is measured on that path).
  * `feature_normalization: current-frame RMS, no temporal statistics`,
    `input_level: unchanged` -> unlike DPDFNet (ROADMAP §2.1) there is no
    running normaliser to mis-seed and no level dependence to expect.
  * `intended_total_latency_samples: 256` -> up to one hop of queueing plus
    the one-hop model delay, ~5.8 ms at 44.1 kHz.

Trained on MUSDB18-HQ + MoisesDB, both non-commercial datasets: the weights
are MIT-labelled, but that is a dataset-terms question to settle before this
is bundled into the .deb (it deliberately is not, yet).
"""

import numpy as np

from .base import StreamProcessor
from .ort_util import make_session

HOP = 128
VOCALS = 2                # source_order: drums, bass, vocals, other
AUDIO_IN = "audio_chunk"
FULL_OUT = "separated_chunk"

_EMPTY = np.zeros((0, 2), dtype=np.float32)


class StemgenProcessor(StreamProcessor):
    name = "stemgen"
    sample_rate = 44100
    latency_samples = 2 * HOP
    wants_stereo = True

    def __init__(self, model_path: str):
        self.session = make_session(model_path)
        ins = self.session.get_inputs()
        outs = [o.name for o in self.session.get_outputs()]
        self._states = [i for i in ins if i.name != AUDIO_IN]
        # next_<state> comes back for every <state> fed; carry them by name
        # so the extract and the full graph need no separate code paths.
        self._next = {f"next_{i.name}": i.name for i in self._states}
        missing = [n for n in self._next if n not in outs]
        if missing:
            raise ValueError(f"{model_path}: no state output for {missing}")
        audio = [o for o in outs if o not in self._next]
        if len(audio) != 1:
            raise ValueError(f"{model_path}: expected one audio output, got {audio}")
        self._audio_out = audio[0]
        self._full = self._audio_out == FULL_OUT
        self._outs = [self._audio_out] + list(self._next)
        self.reset()

    def reset(self) -> None:
        self._feeds = {i.name: np.zeros(i.shape, dtype=np.float32)
                       for i in self._states}
        self._pending = _EMPTY
        self._primed = False

    def _hop(self, chunk: np.ndarray) -> np.ndarray:
        self._feeds[AUDIO_IN] = np.ascontiguousarray(chunk.T[None])
        res = self.session.run(self._outs, self._feeds)
        for name, val in zip(self._outs[1:], res[1:]):
            self._feeds[self._next[name]] = val
        y = res[0][0, VOCALS] if self._full else res[0]
        return y.reshape(2, HOP).T

    def feed(self, x: np.ndarray) -> np.ndarray:
        if x.ndim != 2 or x.shape[1] != 2:
            raise ValueError(f"wants_stereo processor needs (n, 2), got {x.shape}")
        self._pending = np.concatenate([self._pending, x.astype(np.float32)])
        out = []
        while len(self._pending) >= HOP:
            chunk, self._pending = self._pending[:HOP], self._pending[HOP:]
            y = self._hop(chunk)
            if not self._primed:
                # The first return is the one invalid pre-roll hop; dropping
                # it is what makes output[n] the processed input[n].
                self._primed = True
                continue
            out.append(y)
        return np.concatenate(out).astype(np.float32) if out else _EMPTY
