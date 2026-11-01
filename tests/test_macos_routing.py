#!/usr/bin/env python3
"""macOS routing state machine, with Core Audio faked out.

No Mac, no tap, no TCC permission, no audio hardware. The real ctypes layer
(CoreAudio in backends/macos.py) is exactly the part that cannot be tested
anywhere else; this file tests the decisions above it: enable/disable,
trap loss, following a changed output, the debounce, and the rebuild in
resolve_stream_devices().

The live tap itself is covered by tests/test_macos_live.py and the record in
docs/MACOS.md.

Run: .venv/bin/python tests/test_macos_routing.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends import macos  # noqa: E402
from assassin_live.audio.backends.base import SinkInfo  # noqa: E402

FAILURES = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" +
          (f"  — {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeCoreAudio:
    """The small behaviour MacOSBackend needs, with no ctypes in sight."""

    def __init__(self, devices, default_uid):
        self.devices = {d.uid: d for d in devices}
        self.default_uid = default_uid
        self.taps = {}
        self.aggregates = {}
        self.tap_error = None
        self.aggregate_error = None
        self.next_id = 100
        self.destroyed_taps = []
        self.destroyed_aggregates = []

    def default_output(self):
        return self.devices.get(self.default_uid)

    def output_devices(self):
        return list(self.devices.values())

    def device_exists(self, uid):
        return uid in self.devices

    def pid_to_process_object(self, pid):
        return 42

    def create_tap(self, output_uid, pid):
        if self.tap_error:
            return None, self.tap_error
        self.next_id += 1
        tap = macos.Tap(self.next_id, f"tap-{self.next_id}")
        self.taps[tap.id] = tap
        return tap, None

    def destroy_tap(self, tap_id):
        self.destroyed_taps.append(tap_id)
        self.taps.pop(tap_id, None)
        return True

    def tap_exists(self, tap_id):
        return tap_id in self.taps

    def create_aggregate(self, output_uid, tap_uid):
        if self.aggregate_error:
            return None, self.aggregate_error
        self.next_id += 1
        aggregate = macos.Aggregate(
            self.next_id,
            f"{macos.AGGREGATE_UID_PREFIX}.{self.next_id}",
            f"{macos.AGGREGATE_NAME} {self.next_id}")
        self.aggregates[aggregate.id] = aggregate
        return aggregate, None

    def destroy_aggregate(self, agg_id):
        self.destroyed_aggregates.append(agg_id)
        self.aggregates.pop(agg_id, None)
        return True

    def aggregate_exists(self, agg_id):
        return agg_id in self.aggregates


class FakeSounddevice:
    def __init__(self, core):
        self.core = core
        self.terminated = self.initialized = 0

    def _terminate(self):
        self.terminated += 1

    def _initialize(self):
        self.initialized += 1

    def query_devices(self):
        devices = []
        for aggregate in self.core.aggregates.values():
            devices.append({
                "name": aggregate.name,
                "max_input_channels": 2,
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            })
        for device in self.core.output_devices():
            devices.append({
                "name": device.name,
                "max_input_channels": 0,
                "max_output_channels": 2,
                "default_samplerate": device.rate,
            })
        return devices


def _with_sounddevice(fake, fn):
    real = sys.modules.get("sounddevice")
    sys.modules["sounddevice"] = fake
    try:
        return fn()
    finally:
        if real is None:
            del sys.modules["sounddevice"]
        else:
            sys.modules["sounddevice"] = real


def _with_clock(clock, fn):
    real = macos.time
    macos.time = clock
    try:
        return fn()
    finally:
        macos.time = real


def _backend(core):
    b = macos.MacOSBackend(core)
    return b


def _enabled(core):
    b = _backend(core)
    real = b.enable()
    assert real is not None, b.last_enable_error
    return b


SPEAKERS = macos.Device(72, "BuiltInSpeakerDevice", "MacBook Pro Speakers",
                        48000.0)
BUDS = macos.Device(84, "buds:output", "CMF Buds 2 Plus", 44100.0)


def test_enable_creates_tap_and_aggregate():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    b = _enabled(core)
    check("enable returns the real output", b.real.name == SPEAKERS.uid)
    check("tap and aggregate exist", b.tap is not None and b.aggregate is not None)
    check("tap targets this process", len(core.taps) == 1)
    check("system default was not changed", core.default_uid == SPEAKERS.uid)
    check("check is quiet at steady state", b.check() is None)
    check("asks PortAudio for low latency", b.stream_latency == "low")
    print("  enable        -> tap + private aggregate, default untouched")


def test_disable_destroys_and_is_idempotent():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    b = _enabled(core)
    tap_id, agg_id = b.tap.id, b.aggregate.id
    b.disable()
    check("tap destroyed", tap_id in core.destroyed_taps)
    check("aggregate destroyed", agg_id in core.destroyed_aggregates)
    check("state cleared", b.tap is None and b.aggregate is None)
    b.disable()          # must not raise or try to destroy twice
    check("second disable is safe", len(core.destroyed_taps) == 1)
    print("  disable       -> destroys both, no default restore needed")


def test_no_output_declines():
    core = FakeCoreAudio([], "missing")
    b = _backend(core)
    check("enable declines", b.enable() is None)
    check("reason names output", "output" in (b.last_enable_error or "").lower())
    check("nothing was created", not core.taps and not core.aggregates)
    print("  no output     -> declined with an actionable reason")


def test_permission_error_is_actionable():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    core.tap_error = ("AudioHardwareCreateProcessTap failed with OSStatus "
                      "1852797029 ('nope')")
    b = _backend(core)
    check("enable declines", b.enable() is None)
    text = b.last_enable_error or ""
    check("message names System Audio Recording",
          "System Audio Recording" in text)
    check("message gives the reset command", "tccutil reset AudioCapture" in text)
    print("  no permission -> explains TCC instead of a bare status")


def test_aggregate_failure_cleans_up_tap():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    core.aggregate_error = ("AudioHardwareCreateAggregateDevice failed with "
                            "OSStatus 1852797029 ('nope')")
    b = _backend(core)
    check("enable declines", b.enable() is None)
    check("tap was cleaned up", len(core.destroyed_taps) == 1)
    check("nothing remains", not core.taps and not core.aggregates)
    print("  aggregate fail-> tap destroyed, no half-installed session")


def test_trap_loss_is_reported():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    b = _enabled(core)
    core.taps.clear()
    check("missing tap -> trap_lost", b.check() == "trap_lost")
    b = _enabled(core)
    core.aggregates.clear()
    check("missing aggregate -> trap_lost", b.check() == "trap_lost")
    print("  trap lost     -> reported, not treated as healthy")


def test_follows_output_after_debounce():
    clock = Clock()
    core = FakeCoreAudio([SPEAKERS, BUDS], SPEAKERS.uid)
    b = _enabled(core)

    def go():
        core.default_uid = BUDS.uid
        check("first sighting is not acted on immediately", b.check() is None)
        check("settling is true", b.settling)
        clock.advance(macos.MacOSBackend.RETARGET_DEBOUNCE_S + 0.01)
        check("after debounce -> real_sink_changed",
              b.check() == "real_sink_changed")
        check("real follows the new output", b.real.name == BUDS.uid)
    _with_clock(clock, go)
    print("  output change -> followed after the debounce")


def test_replaced_when_old_device_vanished():
    clock = Clock()
    core = FakeCoreAudio([SPEAKERS, BUDS], SPEAKERS.uid)
    b = _enabled(core)

    def go():
        del core.devices[SPEAKERS.uid]
        core.default_uid = BUDS.uid
        b.check()
        clock.advance(macos.MacOSBackend.RETARGET_DEBOUNCE_S + 0.01)
        check("vanished device -> real_sink_replaced",
              b.check() == "real_sink_replaced")
        check("real moved to the fallback", b.real.name == BUDS.uid)
    _with_clock(clock, go)
    print("  device gone   -> replaced, not mistaken for a user choice")


def test_lost_when_no_output_remains():
    clock = Clock()
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    b = _enabled(core)

    def go():
        core.default_uid = "gone"
        check("no output -> real_sink_lost", b.check() == "real_sink_lost")
    _with_clock(clock, go)
    print("  no output left-> real_sink_lost")


def test_volume_and_retarget_are_no_ops():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    b = _enabled(core)
    check("sync_volume is a no-op", b.sync_volume() is None)
    check("adopt_volume does not raise", b.adopt_volume() is None)
    check("relabel is a no-op", b.relabel(b.real) is False)
    check("retarget_playback declines", b.retarget_playback(os.getpid(),
                                                             SPEAKERS.uid) is False)
    print("  volume        -> native default means no mirror needed")


def test_diagnose_capture_trap_lost():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    b = _enabled(core)
    check("healthy -> None", b.diagnose_capture(os.getpid()) is None)
    core.aggregates.clear()
    check("missing aggregate -> trap_lost",
          b.diagnose_capture(os.getpid()) == "trap_lost")
    print("  diagnose      -> catches a vanished tap/aggregate")


def test_resolve_uses_the_aggregate_and_rebuilds():
    core = FakeCoreAudio([SPEAKERS, BUDS], SPEAKERS.uid)
    b = _enabled(core)
    sd = FakeSounddevice(core)

    def go():
        capture, playback = b.resolve_stream_devices(
            b.monitor_source, b.real.description)
        check("capture is the aggregate", capture == 0)
        check("playback is the same aggregate", playback == 0)
        check("PortAudio was refreshed",
              sd.terminated == 1 and sd.initialized == 1)

        # Simulate check() having followed a device change, then resolve()
        # being called by the engine's stop/start retarget.
        old_agg = b.aggregate.id
        core.default_uid = BUDS.uid
        b.real = BUDS.as_sink()
        capture, playback = b.resolve_stream_devices(
            b.monitor_source, b.real.description)
        check("old aggregate destroyed", old_agg in core.destroyed_aggregates)
        check("new aggregate exists", b.aggregate is not None
              and b.aggregate.id != old_agg)
        check("new aggregate is used", capture == 0)
    _with_sounddevice(sd, go)
    print("  resolve       -> sees the aggregate, rebuilds after a change")


def test_resolve_refuses_missing_aggregate():
    core = FakeCoreAudio([SPEAKERS], SPEAKERS.uid)
    b = _enabled(core)
    sd = FakeSounddevice(core)
    sd.query_devices = lambda: []

    def go():
        try:
            b.resolve_stream_devices(b.monitor_source, b.real.description)
        except RuntimeError as exc:
            check("missing aggregate -> RuntimeError",
                  "PortAudio" in str(exc))
            print("  missing agg   -> refuses rather than opening a wrong device")
            return
        raise AssertionError("opened a stream with no aggregate")
    _with_sounddevice(sd, go)


def main():
    print("\nmacOS routing")
    test_enable_creates_tap_and_aggregate()
    test_disable_destroys_and_is_idempotent()
    test_no_output_declines()
    test_permission_error_is_actionable()
    test_aggregate_failure_cleans_up_tap()
    test_trap_loss_is_reported()
    test_follows_output_after_debounce()
    test_replaced_when_old_device_vanished()
    test_lost_when_no_output_remains()
    test_volume_and_retarget_are_no_ops()
    test_diagnose_capture_trap_lost()
    test_resolve_uses_the_aggregate_and_rebuilds()
    test_resolve_refuses_missing_aggregate()
    print()
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
