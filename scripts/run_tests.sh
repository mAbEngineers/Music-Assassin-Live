#!/usr/bin/env bash
# The suite that can run anywhere: no audio hardware, no PipeWire, no display.
# One entry point for humans and for CI (.github/workflows/tests.yml calls
# this file), so the two cannot drift into testing different things.
#
# WHAT IS DELIBERATELY NOT HERE:
#
#   tests/test_routing_dry.py  -- mutates the live PipeWire graph. It is not
#   "dry" despite the name, it destroys every sink named MusicAssassin, and
#   on 2026-08-18 it did exactly that to a running instance and left the app
#   capturing its own output (ROADMAP C8). It has a guard now, but a guard is
#   not a reason to put it somewhere it runs unattended. Run it by hand.
#
#   tests/test_live_e2e.py     -- takes over the system default sink.
#
# tests/test_processors_offline.py needs the ONNX models, which are not in
# this repository. It runs when they are present and is REPORTED AS SKIPPED
# when they are not -- never silently dropped, because a suite that quietly
# shrinks is how a check stops covering anything without anyone noticing.
set -u

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"

HARDWARE_FREE=(
  test_engine_recovery      # callback crash containment, soft limiter
  test_engine_chunked       # FIFO caps vs chunked processors, lockstep on trim
  test_capture_resync       # a stall held in the device buffers, and the resync
  test_mask_shaper          # suppression floor + smoothing never amplify, count-aligned
  test_stereo_rebuild       # B3's mask rebuild + measured latency (C4/C7)
  test_capture_diagnosis    # C8, incl. the pulse-proxied pid trap
  test_retarget_live        # C1's live move and its fallbacks
  test_live_model_swap      # live model switch + the supervision heartbeat
  test_routing_events       # check()'s four outcomes (C1/C3)
  test_status_line          # C4's health word
  test_volume_mirror        # C2's mirror
  test_platform_guard       # the startup refusal, and the crash it replaces
  test_windows_routing      # D3.3's state machine, with COM faked out
  test_windows_com_thread   # COM on a worker thread (Windows only; skips here)
  test_icon_assets          # the .ico matches the icon the app draws (needs Pillow)
)

fail=0
skipped=()
echo "== hardware-free suite =="
for t in "${HARDWARE_FREE[@]}"; do
  printf '  %-26s ' "$t"
  if out=$("$PY" "tests/$t.py" 2>&1); then
    # A test that cannot run here exits 0 after saying so (the Windows COM
    # check off Windows, the icon check without Pillow). Reported as SKIPPED
    # rather than PASS: see the note at the top about suites that shrink.
    if grep -q "SKIPPED" <<<"$out"; then
      echo "$(grep -o 'SKIPPED.*' <<<"$out" | head -1)"
      skipped+=("$t")
    else
      echo "PASS"
    fi
  else
    echo "FAIL"
    echo "$out" | sed 's/^/      /'
    fail=1
  fi
done

echo
echo "== needs the ONNX models =="
# test_spleeter_chunking additionally needs sherpa-onnx, which is NOT an app
# dependency (ROADMAP A1, processors/spleeter.py) -- so it skips on this
# venv and runs on the research one. Both report NOTHING TESTED rather than
# passing empty, which is what the SKIPPED branch keys on.
#
# test_cstft is here for the same reason and not because it needs models: its
# pure-numpy half always runs, but the half that matters -- that the STFT
# scale matches torch's, which MRX's int8 graph depends on -- needs torch,
# which is likewise not an app dependency.
MODEL_TESTS=(
  test_processors_offline   # every installed processor's feed() contract
  test_spleeter_chunking    # A1's separator wrapper: quantum, edges, lag
  test_cstft                # torch-free STFT vs torch (MRX's input scale)
  test_mrx                  # MRX windows, count alignment, keep-effects switch
)
for t in "${MODEL_TESTS[@]}"; do
  printf '  %-26s ' "$t"
  if out=$("$PY" "tests/$t.py" 2>&1); then
    echo "PASS"
  elif grep -q "NOTHING TESTED" <<<"$out"; then
    echo "SKIPPED (models not installed)"
    skipped+=("$t")
  else
    echo "FAIL"
    echo "$out" | sed 's/^/      /'
    fail=1
  fi
done

echo
if [ ${#skipped[@]} -gt 0 ]; then
  echo "skipped: ${skipped[*]}"
fi
if [ "$fail" -eq 0 ]; then
  echo "PASS"
else
  echo "FAIL"
fi
exit "$fail"
