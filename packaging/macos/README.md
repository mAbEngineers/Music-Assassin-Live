# macOS packaging

Builds `dist/Music Assassin Live-<version>.dmg`: a PyInstaller `.app` with the
five redistributable models in `Contents/Resources/models`, wrapped in a DMG.

The app uses native Core Audio process taps. There is no BlackHole, no virtual
driver, and no admin install. The one-time System Audio Recording permission
is the only setup step.

## Requirements

- macOS 14.4+ on Apple Silicon (arm64)
- Python 3.11+ (3.12 recommended) **with Tkinter**, e.g. the python.org build
  or `brew install python-tk@3.12`
- Xcode command-line tools (`xcode-select --install`)
- A code-signing identity. **Ad-hoc signing is not enough for TCC**; use a
  self-signed `Music Assassin Dev` identity for local builds. A Developer ID
  is needed only for a clean public release.

## Build

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt pyinstaller
.venv/bin/python scripts/fetch_models.py
scripts/build_macos.sh
```

`build_macos.sh` renders an `.icns` from
`packaging/icons/music-assassin-live-1024.png`, runs PyInstaller, stages the
models and licences, signs the bundle, and creates the DMG.

Output:

```
dist/Music Assassin Live-0.1.5.dmg
```

### Signing

Create a self-signed code-signing certificate once:

1. **Keychain Access → Certificate Assistant → Create a Certificate**
2. Name: `Music Assassin Dev`
3. Identity Type: *Self Signed Root*
4. Certificate Type: *Code Signing*
5. Create.

Then:

```bash
CODESIGN_IDENTITY="Music Assassin Dev" scripts/build_macos.sh
```

If the certificate lives in a separate keychain:

```bash
CODESIGN_IDENTITY="Music Assassin Dev" \
CODESIGN_KEYCHAIN="$HOME/Library/Keychains/music-assassin-dev.keychain-db" \
scripts/build_macos.sh
```

For a Developer ID build:

```bash
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
NOTARY_PROFILE="notary-profile" \
scripts/build_macos.sh
```

`NOTARY_PROFILE` is a `xcrun notarytool store-credentials` profile. Without it
the DMG is signed but not notarized.

## Download and install

Download `Music Assassin Live-<version>.dmg` from the release page (or the
GitHub Actions artifact on the PR).

1. Open the DMG.
2. Drag **Music Assassin Live** to **Applications**.
3. The build is not notarized if no Developer ID was used. Gatekeeper will
   block the first launch. Either:
   - right-click the app → **Open** → **Open**, or
   - remove the quarantine flag once:

   ```bash
   xattr -dr com.apple.quarantine "/Applications/Music Assassin Live.app"
   ```

4. Launch the app from Applications.
5. macOS asks for **System Audio Recording** permission. Click **Allow**.
   If it does not ask, open **System Settings → Privacy & Security → System
   Audio Recording** (or *Screen & System Audio Recording*) and enable
   **Music Assassin Live**.
6. Choose `dpdfnet_hr` in the Pipeline picker. This is the voice-over model:
   best for a person speaking over background music.
7. Turn the toggle **ON**, then play a video.

If the app is already enabled but you hear nothing, reset the permission and
relaunch:

```bash
tccutil reset AudioCapture com.mabengineers.music-assassin-live
```

### Uninstall

Quit the app, drag **Music Assassin Live** from Applications to the Trash.
The TCC entry can be removed in System Settings. The app never installs a
driver or changes the system default output.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| App launches but no audio and no permission prompt | ad-hoc signature; rebuild with a stable `CODESIGN_IDENTITY` |
| Prompt never appears after rebuilding | `tccutil reset AudioCapture com.mabengineers.music-assassin-live`, then relaunch |
| “App is damaged” / Gatekeeper refuses | run the `xattr` command above, or right-click → Open |
| No sound after enabling | check System Settings → Privacy & Security → System Audio Recording; the app should be listed |
| Music remains under speech | use `dpdfnet_hr`; `stemgen` is gentler on voice but leaves more music |
| Song-stem separation is poor | expected; this app targets voice-over/dialogue over music, not karaoke |
| Bluetooth headphones go silent | disconnect/reconnect; capture works on Bluetooth, but a full output A/B is not yet confirmed |

## CI note

The macOS build is not run in the existing Linux/Windows workflows. A macOS
runner can run `scripts/run_tests.sh` and `scripts/build_macos.sh`; the DMG can
be uploaded as an Actions artifact. Signing in CI needs a certificate secret,
so unsigned/ad-hoc CI artifacts are compile checks, not TCC-working builds.
