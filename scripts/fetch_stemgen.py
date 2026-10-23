#!/usr/bin/env python3
"""Fetch StemgenRT, cut out its vocals model, install it for processors/stemgen.py.

    python scripts/fetch_stemgen.py                  # download v0.7.0 + extract
    python scripts/fetch_stemgen.py --source model.onnx   # use a file you have

The release asset holds three independent causal separators (drums, bass,
vocals). Only vocals is wanted -- the app keeps the voice and drops the rest
-- and running the other two would triple the cost for output that is thrown
away. `onnx.utils.extract_model` cuts the vocals sub-model out by tensor name;
the result is then checked BIT-IDENTICAL against the full graph's vocals over
a few hundred hops of noise before anything is installed, because an internal
tensor name is exactly the kind of thing a new release renames.

Needs `onnx` (a dev-time dependency only -- the app itself never imports it):
    .venv/bin/pip install onnx

Weights: MIT (sweetspotsoundsystem/stemgen-rt). Trained on MUSDB18-HQ +
MoisesDB, both non-commercial datasets -- see models/stemgen_rt_vocals.json.
"""

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.paths import data_dir  # noqa: E402

VERSION = "v0.7.0"
URL = (f"https://github.com/sweetspotsoundsystem/stemgen-rt/releases/download/"
       f"{VERSION}/model.onnx")
SHA256 = "0a49388dd2a47986ab9a161c8764c22853b4d601c188ac052eb530f076d513c7"
OUT_NAME = "stemgen_rt_vocals.onnx"

STATES = ("audio_history", "local_hidden", "global_hidden",
          "spectral_numerator_tail")
VOCALS_TENSOR = "/vocals/Div_2_output_0"   # vocals before the stems are stacked
VOCALS_INDEX = 2                           # source_order: drums, bass, vocals, other


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def extract(full: Path, out: Path) -> None:
    import onnx.utils
    ins = ["audio_chunk"] + [f"vocals_{s}" for s in STATES]
    outs = [VOCALS_TENSOR] + [f"next_vocals_{s}" for s in STATES]
    onnx.utils.extract_model(str(full), str(out), ins, outs)


def verify(full: Path, voc: Path, hops: int = 300) -> float:
    """Max |difference| between the extract and the full graph's vocals."""
    import onnxruntime as ort
    a = ort.InferenceSession(str(full), providers=["CPUExecutionProvider"])
    b = ort.InferenceSession(str(voc), providers=["CPUExecutionProvider"])
    fa = {i.name: np.zeros(i.shape, np.float32) for i in a.get_inputs()}
    fb = {i.name: np.zeros(i.shape, np.float32) for i in b.get_inputs()}
    oa = [o.name for o in a.get_outputs()]
    ob = [o.name for o in b.get_outputs()]
    rng = np.random.default_rng(0)
    worst = 0.0
    for _ in range(hops):
        x = (0.1 * rng.standard_normal((1, 2, 128))).astype(np.float32)
        fa["audio_chunk"] = fb["audio_chunk"] = x
        ra, rb = a.run(oa, fa), b.run(ob, fb)
        for n, v in zip(oa, ra):
            if n.startswith("next_"):
                fa[n[5:]] = v
        for n, v in zip(ob, rb):
            if n.startswith("next_"):
                fb[n[5:]] = v
        va = ra[0][0, VOCALS_INDEX]
        worst = max(worst, float(np.abs(va - rb[0].reshape(va.shape)).max()))
    return worst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=None,
                    help="an already-downloaded StemgenRT model.onnx")
    ap.add_argument("--target", type=Path, default=None,
                    help="models directory (default: the app's data dir)")
    args = ap.parse_args()
    target = args.target or (data_dir() / "models")
    target.mkdir(parents=True, exist_ok=True)

    try:
        import onnx  # noqa: F401
    except ImportError:
        print("needs the onnx package for extraction: .venv/bin/pip install onnx")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        full = args.source
        if full is None:
            full = Path(tmp) / "model.onnx"
            print(f"downloading {URL}")
            urllib.request.urlretrieve(URL, full)
        got = sha256(full)
        if got != SHA256:
            print(f"SHA-256 mismatch: got {got}, expected {SHA256} ({VERSION})")
            return 1
        print(f"  SHA-256 ok ({VERSION})")

        voc = Path(tmp) / OUT_NAME
        extract(full, voc)
        diff = verify(full, voc)
        if diff != 0.0:
            print(f"extract disagrees with the full graph (max |diff| {diff:.3g}); "
                  f"not installing — has {VERSION}'s graph changed?")
            return 1
        print("  vocals extract is bit-identical to the full graph")
        shutil.copy2(voc, target / OUT_NAME)
    print(f"installed {target / OUT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
