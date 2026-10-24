#!/usr/bin/env python3
"""Per-clip bench on a slice of the corpus, in parallel shards.

tests/bench_quality.py averages. That is right for ranking configs and wrong
for "which clips does this model fail on", which is the question behind the
"dpdfnet_hr cuts some girl vocals" complaint (ROADMAP, 2026-07-24). This
driver calls the bench's own evaluate(), so every number means what it means
there, and keeps one row per clip and ratio.

    # 6 shards in parallel, then the table
    for k in 0 1 2 3 4 5; do
      setsid nohup .venv/bin/python scripts/bench_subset.py --category anime_op_stereo \\
          --limit 12 --models stemgen,dpdfnet_hr --shard $k/6 --out OUT \\
          --dump-audio OUT/audio > OUT/shard$k.log 2>&1 < /dev/null &
    done
    .venv/bin/python scripts/bench_subset.py --summarize OUT

Logs go to local disk, never the sftp mount (see docs/CURRENT_STATE.md).
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

METRICS = ("delta_si_sdr_db", "vocal_ret_db", "music_supp_db", "sar_db",
           "musical_noise", "stereo_width_db")


def pick(entries: list[dict], limit: int) -> list[dict]:
    """An even stride through the (sorted) category, not the first `limit`."""
    entries = sorted(entries, key=lambda e: e["name"])
    if limit <= 0 or limit >= len(entries):
        return entries
    idx = np.linspace(0, len(entries) - 1, limit).round().astype(int)
    return [entries[i] for i in dict.fromkeys(idx)]


def run(args) -> int:
    import bench_quality as bq
    from assassin_live.paths import models_dir

    manifest = json.loads((args.corpus / "manifest.json").read_text())
    entries = [e for e in manifest["items"]
               if not e.get("holdout") and e.get("category", "general") == args.category]
    entries = pick(entries, args.limit)
    jobs = [(e["name"], tag, model) for e in entries for tag in e["mixes"]
            for model in args.models.split(",")]
    k, n = (int(x) for x in args.shard.split("/"))
    jobs = jobs[k::n]
    args.out.mkdir(parents=True, exist_ok=True)
    if args.dump_audio:
        args.dump_audio.mkdir(parents=True, exist_ok=True)
    mdir = models_dir()
    if args.references:
        # input / ceiling / oracle audio for scripts/visualize_stages.py. Cheap,
        # so one process does them all; no scores are kept.
        for e in entries:
            for tag in e["mixes"]:
                for kind in ("input", "ceiling", "oracle"):
                    bq.evaluate_reference(args.corpus / e["name"], tag, kind, args.dump_audio)
                print(f"  references {tag} {e['name'][:48]}", flush=True)
        return 0
    out = args.out / f"results_{k}.jsonl"
    done = set()
    if out.exists():
        done = {(r["clip"], r["tag"], r["model"])
                for r in map(json.loads, out.read_text().splitlines())}
    print(f"shard {k}/{n}: {len(jobs)} evaluations, {len(done)} already done", flush=True)
    for name, tag, model in jobs:
        if (name, tag, model) in done:
            continue
        cfg = {**bq.DEFAULT_CFG, "model": model}
        try:
            res = bq.evaluate(cfg, args.corpus / name, tag, mdir, args.dump_audio)
        except Exception as e:  # noqa: BLE001 — one bad clip must not end a shard
            res = {"error": f"{type(e).__name__}: {e}"}
        row = {"clip": name, "tag": tag, "model": model,
               **{k2: v for k2, v in res.items()
                  if isinstance(v, (int, float, str, list)) or v is None}}
        with open(out, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"  {model:11s} {tag} {name[:48]}", flush=True)
    return 0


def summarize(d: Path) -> int:
    rows = [json.loads(line) for p in sorted(d.glob("results_*.jsonl"))
            for line in p.read_text().splitlines()]
    rows = [r for r in rows if "error" not in r]
    models = sorted({r["model"] for r in rows})
    print(f"{len(rows)} evaluations, models: {', '.join(models)}\n")
    print(f"{'model':12s} {'n':>3s} " + " ".join(f"{m[:14]:>14s}" for m in METRICS))
    for m in models:
        mr = [r for r in rows if r["model"] == m]
        print(f"{m:12s} {len(mr):3d} " + " ".join(
            f"{np.mean([r.get(x, np.nan) for r in mr]):14.2f}" for x in METRICS))
    clips = sorted({(r["clip"], r["tag"]) for r in rows})
    print("\nper clip, vocals-only probe (vocal_ret_db: 0 = undamaged), worst first by the first model")
    print(f"{'clip':50s} {'tag':4s} " + " ".join(f"{m[:11]:>11s}" for m in models)
          + "   dSI-SDR " + " / ".join(m[:6] for m in models))

    def get(c, t, m, key):
        for r in rows:
            if (r["clip"], r["tag"], r["model"]) == (c, t, m):
                return r.get(key, float("nan"))
        return float("nan")
    for c, t in sorted(clips, key=lambda ct: get(*ct, models[-1], "vocal_ret_db")):
        print(f"{c[:50]:50s} {t:4s} "
              + " ".join(f"{get(c, t, m, 'vocal_ret_db'):11.2f}" for m in models)
              + "   " + " / ".join(f"{get(c, t, m, 'delta_si_sdr_db'):6.2f}" for m in models))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path,
                    default=Path.home() / ".local/state/music-assassin/bench/corpus")
    ap.add_argument("--category", default="anime_op_stereo")
    ap.add_argument("--limit", type=int, default=12, help="clips, evenly strided; 0 = all")
    ap.add_argument("--models", default="stemgen,dpdfnet_hr")
    ap.add_argument("--shard", default="0/1", help="K/N")
    ap.add_argument("--out", type=Path, default=Path("bench_subset_out"))
    ap.add_argument("--dump-audio", type=Path, default=None)
    ap.add_argument("--references", action="store_true",
                    help="only dump input/ceiling/oracle audio for the picked clips")
    ap.add_argument("--summarize", type=Path, default=None, metavar="DIR")
    args = ap.parse_args()
    return summarize(args.summarize) if args.summarize else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
