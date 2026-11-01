#!/usr/bin/env bash
# Build the macOS .app and DMG.
#
# Usage:
#   scripts/build_macos.sh [MODEL_SOURCE_DIR]
#
# MODEL_SOURCE_DIR defaults to $MUSIC_ASSASSIN_MODELS, then
# ~/Library/Application Support/music-assassin/models.
#
# Signing:
#   CODESIGN_IDENTITY  code-signing identity (default: "Music Assassin Dev"
#                      if present in the login keychain, else ad-hoc)
#   CODESIGN_KEYCHAIN  optional keychain containing the identity
#   NOTARY_PROFILE     optional notarytool keychain profile; if set, the DMG
#                      is submitted and stapled
#
# A stable identity is required for the System Audio Recording TCC prompt.
# Ad-hoc signing is a compile check, not a working audio build. See
# packaging/macos/README.md.
#
# Output: dist/Music Assassin Live-<version>.dmg
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PKG="music-assassin-live"
APP_NAME="Music Assassin Live"
VENV="$ROOT/.venv"
BUILD_DIR="${BUILD_DIR:-$ROOT/build/macos}"
DIST_DIR="$ROOT/dist"
MODEL_SOURCE_DIR="${1:-${MUSIC_ASSASSIN_MODELS:-$HOME/Library/Application Support/music-assassin/models}}"
SPEC="$ROOT/packaging/macos/$PKG.spec"
APP="$BUILD_DIR/pyinstaller/dist/$APP_NAME.app"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: this script builds a macOS app and must run on macOS." >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "error: the macOS build currently supports Apple Silicon (arm64) only." >&2
  exit 1
fi
MACOS_VERSION="$(sw_vers -productVersion)"
if ! python3 - "$MACOS_VERSION" <<'PY'
import sys
parts = tuple(int(p) for p in sys.argv[1].split(".")[:2])
raise SystemExit(0 if parts >= (14, 4) else 1)
PY
then
  echo "error: macOS 14.4 or later is required (this is $MACOS_VERSION)." >&2
  exit 1
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "error: no .venv. Run: python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
"$VENV/bin/python" - <<'PY'
import tkinter  # noqa: F401
import onnxruntime  # noqa: F401
import sounddevice  # noqa: F401
PY
if ! "$VENV/bin/python" -c "import PyInstaller" >/dev/null 2>&1; then
  echo "==> installing pyinstaller (build-time only)"
  "$VENV/bin/pip" install pyinstaller
fi

VERSION="$("$VENV/bin/python" - <<'PY'
import pathlib, re
text = pathlib.Path('assassin_live/__init__.py').read_text()
print(re.search(r'__version__ = "([^"]+)"', text).group(1))
PY
)"
DMG="$DIST_DIR/$APP_NAME-$VERSION.dmg"

MODEL_FILES=(
  gtcrn_simple.onnx dpdfnet_baseline.onnx dpdfnet2_48khz_hr.onnx
  dtln_model_1.onnx dtln_model_2.onnx stemgen_rt_vocals.onnx
)
MODEL_CARDS=(
  gtcrn_simple.json dpdfnet_baseline.json dpdfnet2_48khz_hr.json
  dtln.json stemgen_rt_vocals.json
)
for f in "${MODEL_FILES[@]}"; do
  if [[ ! -f "$MODEL_SOURCE_DIR/$f" ]]; then
    echo "error: missing $MODEL_SOURCE_DIR/$f" >&2
    echo "       run scripts/fetch_models.py first, or pass a model dir." >&2
    exit 1
  fi
done

echo "==> building $APP_NAME $VERSION (arm64)"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/icon.iconset" "$DIST_DIR"

echo "==> rendering .icns"
ICON_SRC="$ROOT/packaging/icons/${PKG}-1024.png"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$ICON_SRC" --out "$BUILD_DIR/icon.iconset/icon_${size}x${size}.png" >/dev/null
  sips -z "$((size * 2))" "$((size * 2))" "$ICON_SRC" \
    --out "$BUILD_DIR/icon.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$BUILD_DIR/icon.iconset" -o "$BUILD_DIR/icon.icns"

echo "==> running PyInstaller"
MAL_VERSION="$VERSION" MAL_ICON="$BUILD_DIR/icon.icns" \
  "$VENV/bin/python" -m PyInstaller --clean --noconfirm \
  --distpath "$BUILD_DIR/pyinstaller/dist" \
  --workpath "$BUILD_DIR/pyinstaller/work" \
  "$SPEC" > "$BUILD_DIR/pyinstaller.log" 2>&1 \
  || { tail -80 "$BUILD_DIR/pyinstaller.log"; exit 1; }

if [[ ! -d "$APP" ]]; then
  echo "error: PyInstaller did not produce $APP" >&2
  tail -40 "$BUILD_DIR/pyinstaller.log" >&2
  exit 1
fi

echo "==> staging models"
mkdir -p "$APP/Contents/Resources/models" \
         "$APP/Contents/Resources/model-licenses"
for f in "${MODEL_FILES[@]}"; do
  install -m 644 "$MODEL_SOURCE_DIR/$f" "$APP/Contents/Resources/models/$f"
done
for f in "${MODEL_CARDS[@]}"; do
  install -m 644 "$ROOT/models/$f" "$APP/Contents/Resources/models/$f"
done
for f in "$ROOT"/packaging/model-licenses/*.txt; do
  install -m 644 "$f" "$APP/Contents/Resources/model-licenses/$(basename "$f")"
done
install -m 644 "$ROOT/LICENSE" "$APP/Contents/Resources/LICENSE"

# Models are copied after PyInstaller signs the bundle, so sign last.
IDENTITY="${CODESIGN_IDENTITY:-}"
KEYCHAIN="${CODESIGN_KEYCHAIN:-}"
if [[ -z "$IDENTITY" ]]; then
  if security find-identity -v -p codesigning 2>/dev/null | grep -q "Music Assassin Dev"; then
    IDENTITY="Music Assassin Dev"
  else
    IDENTITY="-"
  fi
fi
echo "==> signing with: ${IDENTITY}"
SIGN_ARGS=(--force --deep --sign "$IDENTITY")
if [[ -n "$KEYCHAIN" ]]; then
  SIGN_ARGS+=(--keychain "$KEYCHAIN")
fi
if [[ "$IDENTITY" != "-" ]]; then
  # A stable self-signed identity is enough for TCC. Developer ID builds get
  # hardened runtime + timestamp so they can be notarized.
  if [[ "$IDENTITY" == Developer\ ID* ]]; then
    SIGN_ARGS+=(--options runtime --timestamp)
  fi
else
  echo "warning: ad-hoc signing. The TCC System Audio Recording prompt may" >&2
  echo "         not appear. Use CODESIGN_IDENTITY for a real build." >&2
fi
codesign "${SIGN_ARGS[@]}" "$APP"
codesign --verify --deep --strict "$APP"
echo "    signature: $(codesign -dv --verbose=2 "$APP" 2>&1 | grep -E 'Authority|Signature|Identifier' | tr '\n' ' ')"

echo "==> building DMG"
DMG_STAGE="$BUILD_DIR/dmg"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp -R "$APP" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "$APP_NAME" -srcfolder "$DMG_STAGE" \
  -ov -format UDZO "$DMG" >/dev/null

if [[ -n "${NOTARY_PROFILE:-}" ]]; then
  echo "==> notarizing"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
fi

echo
echo "built: $DMG"
echo "app:   $APP"
echo
echo "Install: open the DMG, drag Music Assassin Live to Applications, then"
echo "follow packaging/macos/README.md for the Gatekeeper/TCC first run."
