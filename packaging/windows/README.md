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

What is **not** verified, in rough order of what to try first:

1. **Whether the volume mirror double-attenuates.** On Linux it is measured
   that a sink's volume does not scale its monitor, so the trap's level is
   harmless to the model. Whether a virtual cable's render volume scales what
   its capture side produces is a property of that driver and nobody has
   measured it. If it does, lowering the volume attenuates twice -- once in
   what the model is fed, once on the real endpoint -- and the keys feel
   roughly twice as strong. The fix is in `sync_volume()`'s docstring.
2. **The installer's VB-CABLE sequence**, which has compiled but never run.
3. That audio actually flows end to end. That needs a
Windows machine with a cable installed, and no CI runner has either — GitHub
runners report zero audio endpoints. Until someone runs it, Windows support
is written and plausible, not proven.

CI builds now carry the real model weights, fetched from the `models-v1`
release against pinned SHA-256s (`scripts/fetch_models.py`), so the artifact
is an app that can actually process audio rather than a compile check.

Also still open: the installer prompts for VB-CABLE with a single message box
rather than downloading it (see the VB-CABLE note below), and builds are
unsigned, so SmartScreen will warn on first run.

## Before shipping a build with VB-CABLE bundled

VB-CABLE (the virtual audio driver used to replace the Windows default
output device, same role PipeWire's trap sink plays on Linux) is free to use
but its licensing terms ask for a distribution agreement above personal-use
volume — see https://vb-audio.com/Services/licensing.htm. **Do not put
`VBCABLE_Setup_x64.exe` in `vendor/` and ship publicly until that's
resolved.** Draft outreach: `vb-audio-permission-email.md` in this
directory — review, edit, and send it yourself; nothing here sends it for
you.

Until that's settled, leave `vendor/` empty. That is now the *normal* case,
not a degraded one: the installer downloads VB-CABLE at install time instead
of carrying it.

## How the driver gets installed

One `.exe`. The installer fetches
`VBCABLE_Driver_Pack45.zip` from VB-Audio, checks it against a SHA-256 pinned
in the `.iss`, unpacks it and runs `VBCABLE_Setup_x64.exe -i -h`, then asks
for the reboot the driver needs. Nothing of VB-Audio's is redistributed, so
the >10-unit threshold above is never engaged.

Three constraints shaped that, all worth knowing before editing it:

- **Inno Setup 6.4.3 cannot extract `.zip`.** `Extract7ZipArchive` handles
  `.7z` only; `ExtractArchive` with zip support arrived in 6.5, which this
  project avoids on licensing grounds (see below). VB-CABLE ships as a zip,
  so extraction uses a Windows built-in: `tar.exe` (bsdtar, since Windows
  10/1803), falling back to PowerShell's `Expand-Archive`.
- **The url is versioned**, so the pin is against a fixed file rather than a
  moving target. When VB-Audio publishes Pack46 this keeps working against
  45 rather than silently trusting something new. Bumping it is a deliberate
  edit: change `CableUrl`, `CableZipName` and `CableSha256` together.
- **One prompt cannot be removed.** Windows shows its own "install this
  device software?" dialog for any driver, and VB-Audio have confirmed it
  cannot be suppressed. `-i -h` silences *their* installer, not the OS. The
  installer warns the user it is coming so it does not look like a hang.

Every failure path falls back to the old behaviour -- a message box with the
manual steps and a link -- including a hash mismatch, which is reported as
"the installer needs updating" rather than retried. An unverified download is
never run. Offline machines therefore still install the app fine; they just
have to add the driver by hand, exactly as before.

`BundleVBCable=1` still exists for offline/internal builds and now shares the
same code path, minus the download.

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
