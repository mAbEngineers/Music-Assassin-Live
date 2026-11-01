#!/usr/bin/env python3
"""Fetch the redistributable model weights from the models-v1 release.

Why this exists: the .onnx weights are too large to commit (models/.gitignore
says so) and until now the only way to get them was scripts/import_models.py
copying out of a sibling Music-Assassin checkout. That works on the one
machine that has both, and nowhere else -- which is why every CI build so far
has run with ALLOW_NO_MODELS=1 and produced an installer that cannot process
audio. Publishing the weights as release assets (ROADMAP E5) and fetching
them here is what makes a CI build a real app.

WHAT IT WILL NOT FETCH, and why it is a hardcoded list rather than "whatever
is in the release": speechdenoiser has no upstream license at all, and mrx's
training corpus terms are unchecked. Both are usable locally if you supply
the file yourself; neither may ship. See models/README.md for the audit.

THE HASHES ARE THE POINT. A model file is an input to a signal-processing
pipeline that runs on the user's machine, fetched over the network at build
time. Pinning every SHA-256 means a tampered or truncated asset fails the
build instead of shipping. Nothing is moved into place before its hash
matches, so an interrupted download cannot leave a half-file behind that
looks installed.

Standard library only, deliberately: CI runs this before the venv exists.

Usage:
    python3 scripts/fetch_models.py                # into the XDG data dir
    python3 scripts/fetch_models.py --dest DIR     # e.g. a CI staging dir
    python3 scripts/fetch_models.py --check        # verify, download nothing
    python3 scripts/fetch_models.py --list         # print the manifest
"""

import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "mAbEngineers/music-assassin-live"
RELEASE_TAG = "models-v1"
BASE_URL = f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}"

# (filename, size in bytes, sha256). Sizes are checked too: a proxy serving an
# HTML error page with a 200 fails on length long before the hash, which makes
# the failure far easier to read.
MANIFEST = (
    ("gtcrn_simple.onnx", 535638,
     "e77603ac0c23dac3227dd2d7135b3a585cbee2679048aecfa886657d3ae1b534"),
    ("dpdfnet_baseline.onnx", 8791035,
     "debaf0e8893479fca91b8b4c1eae8db195aa8980ccc9012f5809fde2b738151a"),
    ("dpdfnet2_48khz_hr.onnx", 10596848,
     "0b399f8a58dc4d70d8cd97541f5c39869406145193b957d00a03b66070944928"),
    ("dtln_model_1.onnx", 1458237,
     "22b91cae3855e5a0620e66a917ca6c82c58db0e842c770f58d86751c5e8d4ae3"),
    ("dtln_model_2.onnx", 2510010,
     "e20c92f9233fccf29cddf86970d0d0161a03aebccc26d6f4d5639c4d5ec2e639"),
    # The vocals-only subgraph that scripts/fetch_stemgen.py derives from
    # upstream's full four-stem model -- not upstream's own file. The derived
    # one is what build_deb.sh ships and what models/stemgen_rt_vocals.json
    # describes, so it is published here too: one fetch path for a build, and
    # no need for onnx to be installed just to do graph surgery in CI.
    # Bundling it was a maintainer decision (models/README.md, 2026-10-24);
    # its training corpora, MUSDB18-HQ and MoisesDB, are non-commercial.
    ("stemgen_rt_vocals.onnx", 11068704,
     "924e59ac14833483cefc870fbad11f2a048ad4903bac32139ae0285c039e835f"),
)

RETRIES = 3
CHUNK = 1 << 16

# A carriage-return progress bar is useful at a terminal and unreadable in a
# CI log, where every refresh becomes another line. Checked once rather than
# per chunk.
PROGRESS = sys.stderr.isatty()


def default_dest() -> Path:
    """Mirrors assassin_live.paths.data_dir() / "models" without importing it.

    Standalone on purpose -- see the module docstring. On Windows this lands
    at C:\\Users\\<name>\\.local\\share\\music-assassin\\models, which is what
    scripts/build_windows.bat looks for. (Whether that should become
    %LOCALAPPDATA% is ROADMAP D3.2 and is deliberately not decided here: this
    script must agree with paths.py, not get ahead of it.)
    """
    env = os.environ.get("MUSIC_ASSASSIN_MODELS")
    if env:
        return Path(env)
    if "XDG_DATA_HOME" in os.environ:
        base = Path(os.environ["XDG_DATA_HOME"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".local" / "share"
    return base / "music-assassin" / "models"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def already_good(path: Path, size: int, want: str) -> bool:
    if not path.is_file() or path.stat().st_size != size:
        return False
    return digest(path) == want


def download(name: str, size: int, want: str, dest: Path) -> None:
    """Fetch one asset, verify it, and only then put it in place."""
    url = f"{BASE_URL}/{name}"
    final = dest / name
    # A distinct temp name per file, beside the target so the replace below
    # stays on one filesystem and is therefore atomic.
    part = dest / f".{name}.part"

    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            h = hashlib.sha256()
            got = 0
            with urllib.request.urlopen(url, timeout=60) as r, \
                    open(part, "wb") as out:
                while True:
                    block = r.read(CHUNK)
                    if not block:
                        break
                    out.write(block)
                    h.update(block)
                    got += len(block)
                    if PROGRESS and size:
                        pct = 100.0 * got / size
                        print(f"\r    {name:26s} {pct:5.1f}%  "
                              f"{got / 1e6:6.2f}/{size / 1e6:.2f} MB",
                              end="", file=sys.stderr, flush=True)
            if PROGRESS:
                print(file=sys.stderr)

            if got != size:
                raise ValueError(
                    f"wrong length: expected {size} bytes, got {got}. "
                    "A captive portal or proxy serving an error page is the "
                    "usual cause.")
            if h.hexdigest() != want:
                raise ValueError(
                    f"SHA-256 mismatch\n      expected {want}\n"
                    f"      got      {h.hexdigest()}")
            os.replace(part, final)
            return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            last = e
            # A hash mismatch is not transient, and retrying it would only
            # obscure the finding. Length and network errors are worth a retry.
            fatal = isinstance(e, ValueError) and "SHA-256" in str(e)
            print(f"    attempt {attempt}/{RETRIES} failed: {e}",
                  file=sys.stderr)
            if fatal:
                break
        finally:
            if part.exists() and not final.exists():
                part.unlink(missing_ok=True)
            elif part.exists():
                part.unlink(missing_ok=True)
    raise SystemExit(f"error: could not fetch {name}: {last}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", type=Path, default=None,
                    help="where to install (default: the XDG data dir)")
    ap.add_argument("--check", action="store_true",
                    help="verify what is already there; download nothing")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the local copy verifies")
    ap.add_argument("--list", action="store_true",
                    help="print the manifest and exit")
    args = ap.parse_args()

    if args.list:
        print(f"release {RELEASE_TAG} of {REPO}")
        for name, size, sha in MANIFEST:
            print(f"  {name:26s} {size:>9d}  {sha}")
        return 0

    dest = args.dest or default_dest()

    if args.check:
        print(f"==> verifying models in {dest}")
        missing, bad = [], []
        for name, size, sha in MANIFEST:
            p = dest / name
            if not p.is_file():
                missing.append(name)
                print(f"    MISSING  {name}")
            elif already_good(p, size, sha):
                print(f"    ok       {name}")
            else:
                bad.append(name)
                print(f"    CORRUPT  {name}")
        if bad:
            print(f"\n{len(bad)} file(s) do not match the manifest: "
                  f"{', '.join(bad)}", file=sys.stderr)
            return 1
        if missing:
            print(f"\n{len(missing)} file(s) not downloaded yet: "
                  f"{', '.join(missing)}", file=sys.stderr)
            return 1
        print("\nall models present and verified")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    print(f"==> fetching models from {RELEASE_TAG} into {dest}")
    fetched = skipped = 0
    for name, size, sha in MANIFEST:
        if not args.force and already_good(dest / name, size, sha):
            print(f"    have     {name}")
            skipped += 1
            continue
        download(name, size, sha, dest)
        print(f"    verified {name}")
        fetched += 1

    print(f"\n{fetched} fetched, {skipped} already present -> {dest}")
    print("Licenses: packaging/model-licenses/ (also attached to the release).")
    print("Not fetched, and must not ship: speechdenoiser (no upstream "
          "license), mrx (unchecked corpus terms). See models/README.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
