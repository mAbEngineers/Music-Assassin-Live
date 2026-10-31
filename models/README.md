# Models

ONNX weights are NOT committed. The redistributable ones are published as
release assets on the [`models-v1`](https://github.com/mAbEngineers/music-assassin-live/releases/tag/models-v1)
tag, alongside their upstream license texts.

The normal way to get them, on any machine with a network connection:

```bash
python scripts/fetch_models.py
```

It installs to `~/.local/share/music-assassin/models/` and verifies a pinned
SHA-256 for every file before putting it in place, so a tampered or truncated
asset fails rather than ending up in a build. `--check` re-verifies what is
already there; `--dest DIR` installs somewhere else (that is how CI stages
them). This is what the Windows CI build uses -- before it existed, every CI
build ran with `ALLOW_NO_MODELS=1` and produced an installer that could not
process audio.

The older path still works and does not need the network, if you have the
research repo checked out beside this one:

```bash
python scripts/import_models.py --source ../Music-Assassin/models
```

You can also drop `.onnx` files directly into this folder (dev fallback,
gitignored), or set `MUSIC_ASSASSIN_MODELS=/path/to/models`.

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

Neither stemgen nor mrx is fetched by `scripts/import_models.py`, and mrx is
not in `scripts/fetch_models.py` either; each has its own pinned downloader
(`scripts/fetch_stemgen.py`, `scripts/fetch_mrx.py`) that verifies a
published SHA-256 before installing.

stemgen is an exception as of 2026-10-31: `scripts/fetch_models.py` now
carries it as well, because the Windows build needs it and had no way to get
it (`build_deb.sh` shipped stemgen while `build_windows.bat` did not, so
Windows builds silently carried one model fewer). What is in that manifest is
the *derived* vocals-only subgraph `fetch_stemgen.py` produces, not upstream's
four-stem file -- the same artifact `build_deb.sh` stages and this card
describes. `fetch_stemgen.py` remains the way to re-derive it from upstream;
`fetch_models.py` is the way to get the already-derived copy without needing
`onnx` installed.

`scripts/fetch_models.py` deliberately will not fetch speechdenoiser, and
its manifest is a hardcoded list rather than "whatever is in the release", so
adding an asset to a release cannot quietly put it into a build.

## Where a packaged app looks for them

`scripts/build_deb.sh` bundles the five redistributable models (with their
upstream license texts, under `packaging/model-licenses/`) directly into
the `.deb` at `/usr/share/music-assassin-live/models` — `assassin_live/paths.py`
checks that location as a system-wide fallback, so the packaged app works
immediately with no first-run download or manual copy step.

The Windows installer puts them in `{app}\models`, beside the `.exe`, which
needs its own lookup: in a PyInstaller onefile build `__file__` points inside
the temporary extraction directory, so the repo-relative fallback resolves to
`_MEIPASS` and never sees them. `paths.bundled_models_dir()` handles that case
off `sys.executable`. The `.deb` never needed it, because its path is
absolute, which is why no packaged build had run into it before.
