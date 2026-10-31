# Windows packaging

Builds `dist/music-assassin-live-setup-<version>.exe`: a PyInstaller onefile
binary (same engine as the Linux `.deb`, see `scripts/build_deb.sh`) wrapped
in an Inno Setup installer.

## Status: audio flows on real hardware; see what the first runs found

The Windows routing backend exists: `assassin_live/audio/backends/windows.py`
makes a virtual audio cable the system default via `IPolicyConfig`, captures
the cable's output side, and renders to the real device — the same shape as
the PipeWire trap-sink on Linux. `routing.py` selects it on `win32`.

What is verified, and how:

| Claim | Verified by |
| --- | --- |
| The state machine handles device loss, hotplug, flapping, crash recovery | `tests/test_windows_routing.py` — 56 cases, hardware-free (all COM sits behind `CoreAudio`, which the tests replace) |
| Core Audio works from a worker thread, not only the one that imported comtypes | `tests/test_windows_com_thread.py`, run on Windows CI |
| A crash leaves a traceback and hands the default endpoint back | `tests/test_crash_report.py` |
| `IPolicyConfig` instantiates and switches the default endpoint | `scripts/spike_d3_windows_routing.py`, run on Windows 10 19044 — `SetDefaultEndpoint` returned S_OK for all three roles |
| `pycaw`/`comtypes` import and enumerate on real Windows | `.github/workflows/windows-build.yml` |
| The exe builds and the `.iss` compiles | same workflow |

### What the first hardware runs found

Four faults, all of them in the half CI cannot reach. Recorded because each
one is a class of mistake this port can make again, not a one-off:

1. **COM was not initialised on the worker thread.** `ui/app.py` runs
   `enable()` off the Tk thread; comtypes initialises COM only for the
   thread that imports it, so every COM call there failed and
   `render_endpoints()` returned `[]`. The backend read that, correctly, as
   "no output devices" and said so — naming the one thing that was not
   wrong. Fixed by `_ensure_com()`, per thread.
2. **The error message was a guess.** Four distinct `enable()` failures all
   reported "No hardware audio sink found (Bluetooth asleep?)", a sentence
   written for Linux. Each now says which one it was, and
   `last_enable_error` carries it to the dialog.
3. **A cable was mistaken for a speaker.** VB-CABLE installs
   `CABLE In 16ch` next to `CABLE Input`, and `CABLE Input` is not a
   substring of it, so the trap match missed it and the backend filed it
   under hardware. It could then be rendered into, and — the one the user
   actually hit — handed the system default on the way out, which looks
   like a successful restore and plays nothing. See
   `VIRTUAL_RENDER_NAMES`; the name list is best-effort, because an
   endpoint carries no property saying whether it is real.
4. **A crash left the cable holding the default.** `recover_stale()` only
   ran at startup, so a crash meant a silent machine until the app was
   launched again — and nothing on screen said so. There are now hooks on
   exit, on an unhandled exception, on a dead worker thread and on a Tk
   callback, plus a Start-menu **Restore my speakers** shortcut
   (`--recover`) for when the process is gone entirely.

Diagnostics, which did not exist for any of the above: the build is
PyInstaller `--windowed`, so it has no console and every traceback went
nowhere. `assassin_live/diagnostics.py` now writes to
`%USERPROFILE%\.local\state\music-assassin\crash.log` — Python tracebacks,
including from spawned threads, and `faulthandler` output for a fault
inside onnxruntime or PortAudio, which no Python hook can see. **Ask for
that file first** on any Windows report.

What is **not** verified, in rough order of what to try first:

1. **Whether the volume mirror double-attenuates.** On Linux it is measured
   that a sink's volume does not scale its monitor, so the trap's level is
   harmless to the model. Whether a virtual cable's render volume scales what
   its capture side produces is a property of that driver and nobody has
   measured it. If it does, lowering the volume attenuates twice -- once in
   what the model is fed, once on the real endpoint -- and the keys feel
   roughly twice as strong. The fix is in `sync_volume()`'s docstring.
2. **How long a session survives.** Audio flows — the filter turns on and
   the music goes — but the first long run ended in a crash whose cause is
   not yet known, because nothing was recording it. The crash log above is
   the instrument; the cause is still open.
3. **The installer's VB-CABLE download path**, which has run on one machine
   only. The already-installed path is exercised every re-install.

CI builds now carry the real model weights, fetched from the `models-v1`
release against pinned SHA-256s (`scripts/fetch_models.py`), so the artifact
is an app that can actually process audio rather than a compile check.

Builds are unsigned. See "Unsigned: the two warnings" below — it is a cost
decision, not a defect, and nothing in this repository can remove it.

## Unsigned: the two warnings

Two different dialogs, routinely conflated, with different causes:

- **"Windows protected your PC"** — Microsoft Defender SmartScreen. It
  judges a download on reputation and signature, and a brand-new unsigned
  installer has neither. *More info* → *Run anyway*.
- **"Publisher: Unknown"** on the UAC elevation prompt — no Authenticode
  signature. The installer needs admin (`PrivilegesRequired=admin`, for
  Program Files and the driver), so this prompt appears either way; signing
  only changes the publisher line from Unknown to a name.

Neither is a fault in the build. Things that do **not** fix it:

- `--onedir` instead of `--onefile`. SmartScreen judges the downloaded
  installer, which is unsigned either way. The ROADMAP D3.5 note implying
  otherwise is wrong; onefile's real costs are `%TEMP%` re-extraction on
  every launch and AV heuristics against the extracted exe.
- A self-signed certificate. Untrusted by default, so UAC still reads
  Unknown on any machine that has not installed the root — and teaching a
  user to install a root certificate to silence a warning is worse than the
  warning.
- Submitting the file to Microsoft. That clears a false-positive *block*;
  it does not make an unsigned file signed.

What fixes it is an Authenticode certificate, which costs money and
requires identity verification — a maintainer decision, not a task. Since
June 2023 the private key must live on certified hardware (a USB token or a
cloud HSM), so file-based certificates cheap enough to ignore no longer
exist. Rough shape of the routes, all of which need re-pricing and
re-checking for eligibility before anyone buys one:

| Route | Rough cost | What it gets |
| --- | --- | --- |
| Azure Trusted Signing | ~$10/month | Microsoft-operated, signs from CI; identity validation required, and eligibility rules have changed more than once |
| OV certificate (Sectigo, SSL.com, DigiCert) | ~$200-400/year | Publisher name appears immediately; SmartScreen reputation still accrues with downloads |
| EV certificate | ~$400-700/year | Best SmartScreen behaviour from day one |
| Microsoft Store | listing fee | The Store signs for you, but changes how the app is distributed and sandboxed |

Until one is chosen, say so when handing a build to anyone: the warning is
expected, and a user who is told to expect it is not being trained to click
through warnings in general.

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
- Start Menu shortcuts and an optional desktop one, plus a standard
  uninstaller. The shortcuts go straight into Programs rather than into a
  program group: a group renders as a collapsed *folder* in the Windows
  10/11 Start menu, so the app appeared as a folder to open rather than as
  an app to launch. They also carry an explicit `IconFilename` pointing at
  an installed `.ico` instead of inheriting the exe's embedded icon,
  because Explorer's icon cache keys on the icon source and kept drawing
  the previous artwork for a path it had already seen — the taskbar
  updated, the desktop shortcut did not. `ie4uinit.exe -show` runs
  afterwards to prod the cache as well
  There is deliberately **no** "Restore my speakers" shortcut. One was
  added and removed the same day: the first user to see it read it as a
  second app cluttering the Start menu, which is fair — a recovery tool is
  not an app. Nothing is lost, because launching the app runs
  `recover_stale()` at startup and the uninstaller runs `--recover` before
  deleting anything. The flag stays for use from a terminal.

### What the uninstaller does not do

It does not remove VB-CABLE. That is a separate kernel-mode driver from
VB-Audio, other software may be using it, and uninstalling someone else's
driver is not this installer's business — so `CABLE Input` and
`CABLE In 16ch` stay in the playback device list afterwards, which reads as
a failed uninstall and is not one. The uninstaller now says so, and gives
the actual removal steps (`VBCABLE_Setup_x64.exe` → Remove Driver → reboot).

Because the driver stays, re-running the installer finds a cable already
present and hides the driver task, which looks like the installer
forgetting to ask. The Ready page now states that the step is being skipped
and why. Before files are deleted, the uninstaller also runs the app once
with `--recover --headless`, so uninstalling while the filter is on cannot
leave the cable holding the system default with no app left to hand it back.

### One source of truth for "is a cable installed"

Setup and the app used to answer that question with different tests, and a
user hit the gap: they removed VB-CABLE, reinstalled the app, and setup
skipped the driver step while the app then refused to start for want of a
cable.

- Setup tested for `HKLM\SOFTWARE\VB-Audio\Cable`. VB-CABLE's own *Remove
  Driver* leaves that key behind.
- The app tests for a render endpoint named `CABLE Input`, because that is
  the thing it actually has to use.

`EnsureVBCable` now calls `{app}\music-assassin-live.exe --check-cable` and
believes the answer — exit 0 present, 1 absent, 3 cannot tell (not Windows,
or `pycaw`/`comtypes` missing), with 3 falling back to the registry guess so
an unanswerable question never reads as "install a driver". The registry
check survives for one job only, deciding whether to *offer* the task
before any files exist, and its docstring says so; it was the use of it to
*skip* work that caused this.

When the key is present but the endpoint is not — a pending reboot, or a
removal that left traces — setup asks whether to install or repair rather
than guessing, because the task checkbox was hidden on the strength of that
same key and installing an unoffered driver silently is worse.

## Icon

`packaging/icons/music-assassin-live.ico` is written by
`scripts/draw_icon.py`, from the same master as the PNGs and in the same
run:

```bash
python scripts/draw_icon.py
```

It used to be a separate manual ImageMagick step, and that is exactly how
it went wrong: the icon was redrawn, the `.ico` was not, and a build
shipped with the new artwork in the window and the previous artwork on the
taskbar, in Explorer and in the installer. `tests/test_icon_assets.py`
compares every `.ico` frame against the master so the two cannot drift
again — it needs Pillow, which `tests.yml` installs for that reason.
