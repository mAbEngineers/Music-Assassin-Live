#!/usr/bin/env python3
"""Live macOS routing test: tap → engine → speaker.

NOT hardware-free and NOT safe to run while anything else is using the
speakers: it creates the real process tap and mutes system audio for the
duration. Set ALLOW_LIVE=1, and use headphones or the built-in speakers.

What it proves that the fake-CoreAudio tests cannot:

  * PortAudio can open the tap aggregate with 2 input and 2 output channels
    through the real MacOSBackend;
  * the engine receives non-zero input and emits non-zero output;
  * stopping the engine and disabling the backend leaves no tap behind.

The TCC System Audio Recording permission must already be granted (or the
prompt must be answered when it appears). A silent result is usually a denied
permission, not a bug; `tccutil reset AudioCapture <bundle-id>` re-arms the
prompt for a rebuilt app.

Run: ALLOW_LIVE=1 .venv/bin/python tests/test_macos_live.py
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends import macos           # noqa: E402
from assassin_live.audio.engine import AudioEngine       # noqa: E402
from assassin_live.processors.passthrough import Passthrough  # noqa: E402

SR = 48000


def _tone(path: Path, seconds: float = 8.0) -> None:
    n = int(seconds * SR)
    t = np.arange(n) / SR
    stereo = np.stack([0.2 * np.sin(2 * np.pi * 2000 * t),
                       0.2 * np.sin(2 * np.pi * 3000 * t)], axis=1)
    pcm = np.clip(stereo * 32767, -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def main() -> int:
    if sys.platform != "darwin":
        print("SKIPPED (macOS only)")
        return 0
    if os.environ.get("ALLOW_LIVE") != "1":
        print("SKIPPED: set ALLOW_LIVE=1 to run (this takes over system audio)")
        return 0
    reason = macos.MacOSBackend.unsupported_reason()
    if reason:
        print(f"SKIPPED: {reason}")
        return 0

    tone_path = Path(tempfile.gettempdir()) / "music-assassin-live-test.wav"
    _tone(tone_path)
    tone = subprocess.Popen(["/usr/bin/afplay", str(tone_path)])
    backend = macos.MacOSBackend()
    core = backend._core
    engine = None
    try:
        time.sleep(1)
        real = backend.enable()
        assert real is not None, backend.last_enable_error
        print(f"  output        {real.description} ({real.name})")
        engine = AudioEngine(Passthrough(), backend)
        engine.start(backend.monitor_source, real.name)
        time.sleep(2.5)
        ins = engine.recent_input_levels()
        outs = engine.recent_levels()
        in_peak = max(ins) if ins else 0.0
        out_peak = max(outs) if outs else 0.0
        print(f"  input peak    {in_peak:.5f}")
        print(f"  output peak   {out_peak:.5f}")
        print(f"  latency       {engine.latency_ms:.1f} ms")
        assert in_peak > 1e-4, "no captured audio (TCC denied or wrong output?)"
        assert out_peak > 1e-4, "engine produced no output"
        assert not engine.stats.xruns, f"{engine.stats.xruns} xruns"
        print("  PASS          tap → engine → speaker with non-zero audio")
        return 0
    finally:
        if engine is not None:
            engine.stop()
        backend.disable()
        tone.terminate()
        try:
            tone.wait(timeout=3)
        except subprocess.TimeoutExpired:
            tone.kill()
        time.sleep(0.5)
        left = core.tap_ids()
        print(f"  taps after    {left}")
        if left:
            print("FAIL: a tap was left behind")
            return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(signal.SIGINT + 128)
