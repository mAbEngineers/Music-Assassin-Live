# Icon handover: Music Assassin Live

Written for whoever works on icons in either app, Music Assassin (offline) or
Music Assassin Live. Everything needed is one script, one base image and one
fix in the window code.

## The design (chosen 2026-10-30)

The live app's icon **is the offline app's icon plus two sound waves**, so the
two read as a pair:

- Unchanged from the offline icon: the cream rounded-square plate, the treble
  clef, and the thin red blade that cuts it. Above the cut the clef is grey
  (the music, removed); below it the clef is navy (what is kept).
- Added: two crescent-shaped waves coming off the right side of the clef's
  lower bowl. The inner wave is the clef's navy, the outer one the blade's
  red. Meaning: the cut is made and what is left keeps playing, live.

Each wave is turned to face straight out from the centre of the bowl, so it
lines up with the bowl's curve. They are bold on purpose: at 32 px both still
read as separate strokes. At 16 px only a hint of red survives, which was
true of every idea tried.

## How it was chosen

About 25 variants over five rounds, all on the offline icon, with only one
small element added each time. Rejected along the way, and why:

- A waveform icon on an indigo plate (the first redesign): not related to the
  offline app.
- A red dot with broadcast arcs in the top-right corner, a record badge, a
  "LIVE" pill, equaliser bars: clear at small sizes, but they read as a badge
  stuck on rather than part of the picture. A and C were the runners-up.
- A pulse or wave drawn into the blade itself: elegant at full size, but the
  blade is a hairline once shrunk, so the mark disappears below 64 px.
- Waves concentric with the bowl instead of facing out from it: close
  second; the facing-out version was preferred.
- Thinner waves (the first K7): the navy wave merged into the clef at 32 px.

## Numbers

All in `scripts/draw_icon.py`, top of file, in 1024 px coordinates.

| Name | Value | Meaning |
|---|---|---|
| NAVY | (29, 33, 65) | sampled from the offline clef |
| RED | (194, 37, 51) | sampled from the offline blade |
| BOWL | (527, 606) | centre of a circle fitted to the bowl's outer right edge (radius about 131, max error 4.5 px) |
| WAVES | radius 92 midpoint x 692 navy; radius 142 midpoint x 750 red | arc radius and where each wave's middle sits, at y = 640 |
| HALF_SPAN | 48 degrees | either side of each wave's midpoint |
| WIDTH | 38 px | at the middle of each wave |
| TAPER | 0.4 | width follows sin(pi k / n) ** TAPER; lower keeps the ends fuller |

Each wave is drawn by stamping discs along the arc at 4x size and then
downsampling with Lanczos. Do not draw at final size; the pointed ends alias
badly.

## Files

- `packaging/icons/offline-base-1024.png`: a copy of the offline app's
  `build/icon.png`. The script draws on top of it and needs nothing outside
  this repo. If the offline icon changes, copy the new one here and rerun.
- `scripts/draw_icon.py`: writes `packaging/icons/music-assassin-live-1024.png`
  (the master) and the four window icons `assassin_live/ui/assets/icon.png`
  (128 px), `icon_32.png`, `icon_48.png`, `icon_64.png`. Needs only Pillow.
- `scripts/build_deb.sh` downsizes the master to 16, 22, 24, 32, 48, 64, 128
  and 256 px into the hicolor theme and copies the 64 px one to
  `/usr/share/pixmaps`. Regenerating the master and the window icons is
  enough; the deb script does the rest.

## For the offline repo

Nothing in the offline icon needs to change. If a shared family look is
wanted later, the offline repo could keep this script's `BOWL` fit and colour
constants alongside its own icon source, so both icons are rebuilt from the
same numbers.

## The taskbar bug (worth copying to the offline app)

Symptom: the icon showed in the app menu but the taskbar and dock showed a
generic one.

Cause: on Wayland (Tk runs under XWayland) GNOME matches a window to its
`.desktop` entry by window class. Tk's default class matched nothing, and the
`.desktop` file had no `StartupWMClass`.

Fix, both halves required, identical strings:

- Window: `tk.Tk(className="music-assassin-live")`. With xprop the window then
  reports `WM_CLASS = "music-assassin-live", "Music-assassin-live"`.
- Desktop file: `StartupWMClass=music-assassin-live`.

The offline app needs its own pair, matching its own `.desktop` file name.
After installing, quit and relaunch; if the shell still shows a generic icon,
log out and back in. Confirmed working in Live 0.1.5.

## Pitfalls

- The repo is on an sftp gvfs mount. PyInstaller and `objcopy` fail on it
  (colon in the path, no in-place ELF edits). Build from a copy on local disk
  with `BUILD_DIR` set; see the header comment in `scripts/build_deb.sh`.
- A dev run uses `assassin_live/ui/assets/icon.png`; an installed run uses the
  copy bundled by PyInstaller. Reinstall the deb before judging the taskbar.
- When comparing variants, one large preview plus 64, 32 and 16 px is enough.

## Status

Script, base, master and window icons are in the working tree, uncommitted.
The taskbar fix is confirmed. The R1c icon itself has been seen by the user
only in preview sheets, not yet in the installed app.
