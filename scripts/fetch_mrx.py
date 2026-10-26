#!/usr/bin/env python3
"""Fetch MRX (MERL cocktail-fork separation) for processors/mrx.py.

    python scripts/fetch_mrx.py                      # download + verify
    python scripts/fetch_mrx.py --source mrx.int8.onnx    # use a file you have

MRX is the first three-way dialogue/music/effects separator in this registry,
and the only route to "keep sound effects" live: every other model here emits
one scalar per bin saying how speech-like it is, and drums and sound effects
sit next to each other in that single ranking (docs/IDEAS.md section 1.1). A
model with a separate effects output is a second axis of evidence rather than
a reshuffle of the first.

Unlike scripts/fetch_stemgen.py there is nothing to cut out: the published
file is already exactly the graph the processor runs. What this script does
check, beyond the SHA-256, is that the graph's input and output names are the
ones processors/mrx.py feeds and reads. The offline repo pins this model by
hash for the same reason, and a re-upload that renamed `mag_8192` would
otherwise fail at the first audio block rather than here.

Weights: MIT (merlresearch/cocktail-fork-separation), int8 ONNX mirror at
huggingface.co/audiojs/mrx. See models/mrx.json.
"""

import argparse
import hashlib
import shutil
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.paths import data_dir  # noqa: E402

URL = "https://huggingface.co/audiojs/mrx/resolve/main/mrx.int8.onnx"
SHA256 = "d876c92d224f5d8cd2207d528b23369da895a6de71fb24132416fcb16f9f8424"
SIZE = 31287081
OUT_NAME = "mrx.int8.onnx"

# Kept in step with processors/mrx.py WINDOWS; asserted equal below so the
# two cannot drift apart silently.
WINDOWS = (1024, 2048, 8192)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_graph(path: Path) -> list[str]:
    """-> list of complaints; empty means the graph is what mrx.py expects."""
    import onnxruntime as ort
    from assassin_live.processors.mrx import WINDOWS as PROC_WINDOWS

    bad = []
    if tuple(PROC_WINDOWS) != WINDOWS:
        bad.append(f"processors/mrx.py WINDOWS {tuple(PROC_WINDOWS)} != {WINDOWS}")

    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    ins = {i.name: i for i in sess.get_inputs()}
    outs = {o.name: o for o in sess.get_outputs()}
    for w in WINDOWS:
        if f"mag_{w}" not in ins:
            bad.append(f"no input mag_{w} (has {sorted(ins)})")
        if f"mask_{w}" not in outs:
            bad.append(f"no output mask_{w} (has {sorted(outs)})")
            continue
        # [batch, 3 stems, F, T]: the stem axis is what the processor indexes
        # by MUSIC/SPEECH/SFX, so a 2- or 4-stem re-release must not load.
        shape = outs[f"mask_{w}"].shape
        if len(shape) != 4:
            bad.append(f"mask_{w} rank {len(shape)}, expected 4 ({shape})")
        elif isinstance(shape[1], int) and shape[1] != 3:
            bad.append(f"mask_{w} has {shape[1]} stems, expected 3")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=None,
                    help="an already-downloaded mrx.int8.onnx")
    ap.add_argument("--target", type=Path, default=None,
                    help="models directory (default: the app's data dir)")
    args = ap.parse_args()
    target = args.target or (data_dir() / "models")
    target.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        src = args.source
        if src is None:
            src = Path(tmp) / OUT_NAME
            print(f"downloading {URL}")
            urllib.request.urlretrieve(URL, src)
        size = src.stat().st_size
        if size != SIZE:
            print(f"size mismatch: got {size} bytes, expected {SIZE}")
            return 1
        got = sha256(src)
        if got != SHA256:
            print(f"SHA-256 mismatch: got {got}, expected {SHA256}")
            return 1
        print(f"  SHA-256 ok ({size} bytes)")

        bad = check_graph(src)
        if bad:
            print("graph is not what processors/mrx.py expects; not installing:")
            for b in bad:
                print(f"  - {b}")
            return 1
        print(f"  graph ok: mag_/mask_ pairs for {WINDOWS}, 3 stems")
        shutil.copy2(src, target / OUT_NAME)
    print(f"installed {target / OUT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
