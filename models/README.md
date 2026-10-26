# Models

ONNX weights are NOT committed. Each model ships as a release asset together
with its `model_card.json` (produced by the Music-Assassin research repo's
benchmark harness).

For development, either:

```bash
python scripts/import_models.py --source ../Music-Assassin/models
```

which installs to `~/.local/share/music-assassin/models/`, or drop `.onnx`
files directly into this folder (dev fallback, gitignored), or set
`MUSIC_ASSASSIN_MODELS=/path/to/models`.

The `.json` files here are the model cards for the current model set.

## Licensing (verified 2026-07-24; stemgen and mrx added 2026-10-27)

| model | license | redistributable as release asset? |
|---|---|---|
| gtcrn | MIT | yes — include upstream license text |
| dpdfnet | Apache-2.0 (Ceva-IP) | yes — include license text + attribution |
| dpdfnet_hr | Apache-2.0 (Ceva-IP, same collection as dpdfnet) | yes — include license text + attribution |
| dtln | MIT (breizhn/DTLN) | yes — include upstream license text |
| speechdenoiser | **unresolved** — upstream repo has no license | **no** — see its model card |
| stemgen | MIT (sweetspotsoundsystem/stemgen-rt) | yes — bundled by maintainer decision 2026-10-24, although trained on MUSDB18-HQ + MoisesDB, both non-commercial |
| mrx | MIT (merlresearch/cocktail-fork-separation) | **not yet** — trained on DnR, whose component-corpus terms are unchecked. Same open question as stemgen's, and it wants settling before this ships |

speechdenoiser stays usable for local development (user drops the file in
themselves), but must not ship in releases until upstream adds a license or
the model is re-exported from the dual-licensed DeepFilterNet3 source.

Neither stemgen nor mrx is fetched by `scripts/import_models.py`; each has
its own pinned downloader (`scripts/fetch_stemgen.py`, `scripts/fetch_mrx.py`)
that verifies a published SHA-256 before installing.

`scripts/build_deb.sh` bundles the five redistributable models (with their
upstream license texts, under `packaging/model-licenses/`) directly into
the `.deb` at `/usr/share/music-assassin-live/models` — `assassin_live/paths.py`
checks that location as a system-wide fallback, so the packaged app works
immediately with no first-run download or manual copy step.
