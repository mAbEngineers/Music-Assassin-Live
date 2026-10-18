# Windows packaging

Builds `dist/music-assassin-live-setup-<version>.exe`: a PyInstaller onefile
binary (same engine as the Linux `.deb`, see `scripts/build_deb.sh`) wrapped
in an Inno Setup installer.

## Status: written, compiled, not yet run on real hardware

The Windows routing backend exists: `assassin_live/audio/backends/windows.py`
makes a virtual audio cable the system default via `IPolicyConfig`, captures
the cable's output side, and renders to the real device — the same shape as
the PipeWire trap-sink on Linux. `routing.py` selects it on `win32`.

What is verified, and how:

| Claim | Verified by |
| --- | --- |
| The state machine handles device loss, hotplug, flapping, crash recovery | `tests/test_windows_routing.py` — 29 cases, hardware-free (all COM sits behind `CoreAudio`, which the tests replace) |
| `IPolicyConfig` instantiates and switches the default endpoint | `scripts/spike_d3_windows_routing.py`, run on Windows 10 19044 — `SetDefaultEndpoint` returned S_OK for all three roles |
| `pycaw`/`comtypes` import and enumerate on real Windows | `.github/workflows/windows-build.yml` |
| The exe builds and the `.iss` compiles | same workflow |

What is **not** verified: that audio actually flows end to end. That needs a
Windows machine with a cable installed, and no CI runner has either — GitHub
runners report zero audio endpoints. Until someone runs it, Windows support
is written and plausible, not proven.

Also still open: the installer prompts for VB-CABLE with a single message box
rather than downloading it (see the VB-CABLE note below), builds are unsigned,
and CI builds carry no model weights.

## Before shipping a build with VB-CABLE bundled

VB-CABLE (the virtual audio driver used to replace the Windows default
output device, same role PipeWire's trap sink plays on Linux) is free to use
but its licensing terms ask for a distribution agreement above personal-use
volume — see https://vb-audio.com/Services/licensing.htm. **Do not put
`VBCABLE_Setup_x64.exe` in `vendor/` and ship publicly until that's
resolved.** Draft outreach: `vb-audio-permission-email.md` in this
directory — review, edit, and send it yourself; nothing here sends it for
you.

Until that's settled, leave `vendor/` empty. The installer still builds and
works — it just falls back to prompting the user to install VB-CABLE
manually from vb-audio.com on first run, the same "documented external
dependency" shape the Linux build uses for PipeWire.

## Prerequisites (on a Windows machine — PyInstaller does not cross-compile)

- Python 3.10+, with `.venv` set up and `requirements.txt` installed
  (`python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`)
- [Inno Setup 6.4.3](https://jrsoftware.org/isdl.php) — pin to this version.
  6.5.0+ introduced a paid commercial-license tier; 6.4.3 is the last
  version with zero ambiguity for a free, non-commercial, open-source build
  like this one. Install with `ISCC.exe` on `PATH`, or leave it at the
  default `C:\Program Files (x86)\Inno Setup 6\` — the build script checks
  both.
- Models: same redistributable set `scripts/build_deb.sh` uses (gtcrn,
  dpdfnet, dpdfnet_hr, dtln — `speechdenoiser` excluded, see
  `models/README.md`). Run `scripts\import_models.py` first, or pass a
  source dir directly.

## Build

```batch
scripts\build_windows.bat [MODEL_SOURCE_DIR]
```

Output: `dist\music-assassin-live-setup-<version>.exe`

## What the installer does

- Installs the app to `Program Files\Music Assassin Live`
- Bundles the redistributable models, and sets a per-user
  `MUSIC_ASSASSIN_MODELS` environment variable pointing at them —
  `assassin_live/paths.py` already checks that variable first, so no code
  change was needed for Windows model discovery
- If `packaging\windows\vendor\VBCABLE_Setup_x64.exe` is present at build
  time: bundles it and runs it silently (`-i -h`) during setup. Windows will
  still show one unavoidable driver-signing security prompt — that's an OS
  security gate, not something any installer flag can suppress
- If not present: skips bundling, and after install offers to open the
  VB-CABLE download page for the user
- Start Menu + optional desktop shortcut, standard uninstaller

## Icon

`packaging/icons/music-assassin-live.ico` is generated from the existing
`assassin_live/ui/assets/icon.png` / `packaging/icons/music-assassin-live-1024.png`:

```bash
convert packaging/icons/music-assassin-live-1024.png \
  -define icon:auto-resize=256,128,64,48,32,16 \
  packaging/icons/music-assassin-live.ico
```

Already generated and committed — regenerate only if the source icon
changes.
