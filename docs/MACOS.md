# macOS support

**Status: backend live-tested; `.app`/DMG packaging is in `packaging/macos/`.**
The app runs from source on Apple Silicon today. This page records the design,
the measured behaviour, the TCC/signing requirements, and the honest quality
limits.

## Why native Core Audio taps

macOS has no trap sink to create and no default output to swap. A private
Core Audio **process tap** captures audio destined for the current output, and
`mutedWhenTapped` silences the original only while the tap is being read. A
private aggregate device carries the tap into PortAudio; the existing engine
and models then render the processed result back to the same output.

```
apps -> process tap (muted while read) -> private aggregate
     -> PortAudio at 48 kHz -> existing engine/model -> output device
```

No virtual driver, no admin install, no reboot, no default-device hijack. The
volume keys keep working. If the app stops or crashes, the private objects die
and the original audio returns automatically (`mutedWhenTapped` fail-open), so
`recover_stale()` is deliberately a no-op.

## Requirements

- macOS 14.4 or later, Apple Silicon (arm64). The current ONNX Runtime macOS
  wheels are arm64-only.
- Python 3.11+ (3.12 recommended) with Tkinter for the GUI. A python.org build
  or `brew install python-tk@3.12` provides Tk.
- One-time **System Audio Recording** permission.
- A stable code-signing identity. Ad-hoc signing is **not** enough for the TCC
  prompt; use a local self-signed "Music Assassin Dev" identity for
  development, or a Developer ID for public distribution.

## Backend

`assassin_live/audio/backends/macos.py` implements the full `RoutingBackend`:

| Member | macOS |
|---|---|
| `enable()` | create a device-scoped tap excluding this PID + a private aggregate around the default output |
| `disable()` | destroy aggregate, then tap |
| `check()` | `trap_lost` if either vanishes; follow a changed default after 0.25 s; distinguish `real_sink_changed` from `real_sink_replaced` / `real_sink_lost` |
| `recover_stale()` | no-op by design |
| `relabel()` | `False` (private aggregate has no system label) |
| `sync_volume()` / `adopt_volume()` | no-op (default device unchanged) |
| `retarget_playback()` | `False`; callers use the existing stop/start path |
| `diagnose_capture()` | `trap_lost` if the tap/aggregate is gone |
| `monitor_source` | current private aggregate name |
| `resolve_stream_devices()` | refresh PortAudio, rebuild around the new output if needed |
| `pin_stream()` | `True` (PortAudio indices are authoritative) |

`MacOSBackend.stream_latency = "low"` is read by the engine with `getattr`.
Built-in speakers measured ~63.5 ms added latency with `stemgen` (PortAudio's
default `high` measured ~140 ms); CMF Buds at 44.1 kHz measured ~83.5 ms. Zero
xruns in both. Linux/Windows keep their existing default.

The aggregate inherits the output's native rate. PortAudio requests 48 kHz and
Core Audio resamples; tested on a 44.1 kHz Bluetooth output. **Do not set the
aggregate's nominal rate** — forcing a different rate changed the hardware and
hung PortAudio during the Phase 1 spike.

Output switching was tested both directions (CMF Buds 44.1 kHz ↔ built-in
speakers 48 kHz): the tap/aggregate are rebuilt, PortAudio is refreshed, and
capture is non-zero on both. The 44.1 kHz path is fine.

## TCC, signing, and distribution

- `NSAudioCaptureUsageDescription` must be in `Info.plist`; without it the TCC
  prompt never appears.
- macOS 26+ requires an app bundle; a plain command-line binary does not show
  up in Privacy settings.
- A stable self-signed identity works for development. Ad-hoc signing
  produced a silent capture and no prompt.
- A rebuilt binary may need
  `tccutil reset AudioCapture com.mabengineers.music-assassin-live` before the
  prompt returns.
- Public, no-Apple-account builds: the DMG is not notarized. Follow the
  install instructions in `packaging/macos/README.md` (`xattr`, *Open Anyway*,
  allow System Audio Recording). A Developer ID + notarized build remains the
  clean path.

## Tests

Hardware-free (runs anywhere):

```bash
.venv/bin/python tests/test_macos_routing.py
# or
./scripts/run_tests.sh
```

Covers enable/disable, no output, permission error, aggregate-failure cleanup,
trap loss, output change/debounce, replacement/loss, volume/retarget no-ops,
capture diagnosis, and resolve/rebuild.

Live (takes over system audio; macOS with TCC granted):

```bash
ALLOW_LIVE=1 .venv/bin/python tests/test_macos_live.py
```

Measured on built-in speakers: tap input peak 0.141, engine output peak 0.100,
20 ms latency, zero xruns, zero taps left afterwards. The full app was also
run headless and through the GUI, including Cmd-Q teardown.

## Quality: what this is good at

The primary product case is **a person speaking over background music** —
videos, lectures, streams. On a real YouTube video with the app running
`dpdfnet_hr`, the result was judged **“it actually works pretty well.”**

For that case, use `dpdfnet_hr`: it is the strongest live speech enhancer and
keeps narration understandable under loud rock/pop/instrumental beds. macOS
now defaults to it; Linux/Windows keep `stemgen` as the default.

Karaoke-style **song stem separation is a different, harder problem.** On four
CC BY songs with vocals, no live model was good enough:

- `stemgen` kept the voice but left audible music.
- `dpdfnet_hr` removed more music but was choppy and let music return under
  sustained singing.
- `stemgen → dpdfnet_hr`, `stemgen → gtcrn`, mid/side and 50–200 ms
  smoothing did not beat unsmoothed `dpdfnet_hr`.
- Spleeter (1–4 s chunks) separates better but is not suitable for live video.

So the app does not promise karaoke-grade stem splitting. If that becomes a
goal, it needs a better realtime separator or a lookahead/quality mode, not
another Mac routing tweak.

## Packaging

Build and install instructions, signing details, DMG layout, and troubleshooting
live in [`packaging/macos/README.md`](../packaging/macos/README.md).

## Known limitations

- Bluetooth **output** A/B was not completed; CMF Buds disconnected. Capture,
  44.1 kHz and output switching are verified. If a Bluetooth aggregate does not
  play, the fallback is to capture from the aggregate and render directly to
  the Bluetooth device.
- No menu-bar UI; the existing Tk window is used.
- No Intel build (ONNX Runtime macOS wheel is arm64-only).
- TCC grants are tied to the signing identity; dev rebuilds may need a reset.
