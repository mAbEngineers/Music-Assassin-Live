#!/usr/bin/env python3
"""The Windows routing backend's state machine, on any platform.

Hardware-free and Windows-free by construction. backends/windows.py keeps
all of its COM behind CoreAudio -- three methods -- precisely so the part
above it can be tested here, because the part above it is where the
interesting failures are: a cable that disappears mid-session, a user
picking a different output, a Bluetooth headset flapping, a crash that
leaves the system default pointing at a virtual cable.

What this canNOT test, and nothing on Linux or CI can: whether CoreAudio
itself works. IPolicyConfig is undocumented and CI runners have no audio
endpoints at all, so that half is covered by
scripts/spike_d3_windows_routing.py run on real hardware (done: Windows 10
19044, all three roles returned S_OK). Keep the line between the two halves
where it is -- if logic migrates into CoreAudio, it stops being testable.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from assassin_live.audio.backends import windows                 # noqa: E402
from assassin_live.audio.backends.base import RoutingBackend     # noqa: E402
from assassin_live.audio.backends.windows import WindowsBackend  # noqa: E402

CABLE = "{0.0.0.00000000}.{cable-0000-0000-0000-000000000000}"
SPEAKERS = "{0.0.0.00000000}.{spkrs-1111-1111-1111-111111111111}"
HEADSET = "{0.0.0.00000000}.{hdset-2222-2222-2222-222222222222}"
HDMI = "{0.0.0.00000000}.{hdmi0-3333-3333-3333-333333333333}"
# VB-CABLE's *other* render endpoint, installed alongside CABLE Input and
# present on every machine that has the cable. Its name does not contain
# "CABLE Input", so the trap match misses it -- which is why it is in these
# fixtures rather than only in the one test about it. Anything that treats
# this as a speaker is a bug that ends in silence.
CABLE16 = "{0.0.0.00000000}.{cab16-4444-4444-4444-444444444444}"

FRIENDLY = {
    CABLE: "CABLE Input (VB-Audio Virtual Cable)",
    CABLE16: "CABLE In 16ch (VB-Audio Virtual Cable)",
    SPEAKERS: "Speakers (Realtek(R) Audio)",
    HEADSET: "Headphones (WH-1000XM4 Stereo)",
    HDMI: "Digital Audio (HDMI)",
}


class FakeCoreAudio:
    """A Windows audio endpoint list that tests can rearrange.

    Mimics the real one's contract exactly, including that it never raises:
    set_default_render_endpoint() returns False for an endpoint that is not
    present, the way IPolicyConfig does for a device id Windows does not
    recognise.
    """

    def __init__(self, present=(CABLE, SPEAKERS), default=SPEAKERS):
        self.present = list(present)
        self.default = default
        self.switches = []          # every endpoint id we were asked to set
        self.refuse = False         # simulate IPolicyConfig failing outright
        self.enumerations = 0       # COM round trips, counted
        # endpoint id -> (level 0..1, muted), as IAudioEndpointVolume holds it
        self.volumes = {eid: (0.5, False) for eid in FRIENDLY}
        self.deaf = False           # simulate volume interfaces unavailable

    def render_endpoints(self):
        self.enumerations += 1
        return [(eid, FRIENDLY[eid]) for eid in self.present]

    def default_render_endpoint(self):
        return self.default

    def get_volume(self, endpoint_id):
        if self.deaf or endpoint_id not in self.present:
            return None
        return self.volumes.get(endpoint_id)

    def set_volume(self, endpoint_id, level, muted):
        if self.deaf or endpoint_id not in self.present:
            return False
        self.volumes[endpoint_id] = (max(0.0, min(1.0, level)), bool(muted))
        return True

    def set_default_render_endpoint(self, endpoint_id):
        self.switches.append(endpoint_id)
        if self.refuse or endpoint_id not in self.present:
            return False
        self.default = endpoint_id
        return True


class FakeClock:
    """Stands in for the time module so the debounce is testable without
    sleeping through it."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def backend(core=None, **kw):
    return WindowsBackend(core if core is not None else FakeCoreAudio(**kw))


def _isolated(fn):
    """Run fn(state_path) with ROUTING_STATE pointed at a temp file.

    The backend persists the pre-swap default to a real path from paths.py;
    a test must never read or clobber the developer's own.
    """
    real_state, real_time = windows.ROUTING_STATE, windows.time
    with tempfile.TemporaryDirectory() as d:
        windows.ROUTING_STATE = Path(d) / "routing.json"
        windows.time = FakeClock()
        try:
            return fn(windows.ROUTING_STATE)
        finally:
            windows.ROUTING_STATE, windows.time = real_state, real_time


# -- the seam ---------------------------------------------------------------

def test_satisfies_the_protocol():
    """Structural, not inherited -- base.py is a Protocol on purpose, so
    nothing but a check like this notices a renamed or dropped method."""
    required = [n for n in dir(RoutingBackend)
                if not n.startswith("_") or n in ("__init__",)]
    required = [n for n in required if not n.startswith("__")]
    missing = [n for n in required if not hasattr(WindowsBackend, n)]
    assert not missing, f"WindowsBackend is missing {missing}"
    # the two attributes callers read directly
    b = backend()
    assert b.preferred_name is None and b.real is None
    assert isinstance(b.monitor_source, str) and b.monitor_source
    print(f"  protocol       -> satisfies RoutingBackend ({len(required)} names)")


def test_recover_stale_takes_no_arguments():
    """base.py promises this is callable before any instance exists; both
    __main__.py and ui/app.py call it that way at startup."""
    def go(state):
        WindowsBackend.recover_stale()
    _isolated(go)
    print("  recover_stale  -> callable with no instance and no arguments")


# -- enable / disable -------------------------------------------------------

def test_enable_traps_and_returns_the_real_endpoint():
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        b = backend(core)
        real = b.enable()
        assert real is not None and real.name == SPEAKERS, real
        assert real.description == FRIENDLY[SPEAKERS]
        assert core.default == CABLE, "the cable did not become the default"
        # persisted before the swap, so a crash in between is recoverable
        assert json.loads(state.read_text())["previous_default"] == SPEAKERS
        print("  enable         -> cable is default, speakers returned, state armed")
    _isolated(go)


def test_enable_without_a_cable_declines():
    def go(state):
        core = FakeCoreAudio(present=[SPEAKERS], default=SPEAKERS)
        b = backend(core)
        assert b.enable() is None, "enabled with no virtual cable present"
        assert core.switches == [], "changed the default anyway"
        assert not state.exists(), "armed recovery for a swap it never made"
        print("  no cable       -> declines, touches nothing")
    _isolated(go)


def test_enable_with_nothing_to_play_to_declines():
    """The cable is installed but it is the only endpoint -- playing to it
    would be a feedback loop, so this is the engine's "wait and retry"."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE], default=CABLE)
        b = backend(core)
        assert b.enable() is None, "enabled with no real output endpoint"
        print("  cable only     -> declines rather than trapping into itself")
    _isolated(go)


def test_enable_honours_the_users_choice():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET], default=SPEAKERS)
        b = backend(core)
        b.preferred_name = HEADSET         # as restored from config by ui/app.py
        real = b.enable()
        assert real.name == HEADSET, f"ignored preferred_name: {real}"
        print("  preferred_name -> honoured over the system default")
    _isolated(go)


def test_enable_reports_a_refused_switch():
    """If IPolicyConfig fails there is no trap, and saying otherwise would
    leave the engine streaming into a device nothing is routed to."""
    def go(state):
        core = FakeCoreAudio()
        core.refuse = True
        b = backend(core)
        assert b.enable() is None, "claimed success when the switch failed"
        assert not state.exists(), "left a recovery file for a swap that failed"
        print("  switch refused -> enable() fails loudly, state cleaned up")
    _isolated(go)


def test_each_enable_failure_says_which_one_it_was():
    """All four failures return None, so the return value cannot tell them
    apart -- and ui/app.py showed "No hardware audio sink found (Bluetooth
    asleep?)" for every one of them. That sentence sent the first real
    Windows test looking at its speakers when COM was the problem.
    """
    def go(state):
        cases = []

        core = FakeCoreAudio(present=[], default=None)
        cases.append(("nothing enumerated", backend(core), "fault in the app"))

        core = FakeCoreAudio(present=[SPEAKERS], default=SPEAKERS)
        cases.append(("no cable", backend(core), "no longer installed"))

        core = FakeCoreAudio(present=[CABLE], default=CABLE)
        cases.append(("cable only", backend(core), "no other output device"))

        core = FakeCoreAudio()
        core.refuse = True
        cases.append(("switch refused", backend(core), "refused"))

        seen = []
        for label, b, expected in cases:
            assert b.enable() is None, f"{label}: enabled anyway"
            why = b.last_enable_error
            assert why, f"{label}: failed without saying why"
            assert expected in why, f"{label}: {why!r} does not mention {expected!r}"
            assert "Bluetooth" not in why, f"{label}: still blaming Bluetooth"
            seen.append(why)
            print(f"  {label:<18} -> {why.split('.')[0][:58]}")

        assert len(set(seen)) == len(seen), "two failures give the same message"
    _isolated(go)


def test_a_successful_enable_clears_the_last_error():
    def go(state):
        b = backend(FakeCoreAudio())
        b.last_enable_error = "stale text from a previous attempt"
        assert b.enable() is not None
        assert b.last_enable_error is None, "kept a stale failure message"
        print("  enable ok      -> last_enable_error cleared")
    _isolated(go)


def test_disable_restores_the_real_endpoint():
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        b = backend(core)
        b.enable()
        b.disable()
        assert core.default == SPEAKERS, f"left the default at {core.default}"
        assert not state.exists(), "left a stale recovery file behind"
        assert b.trap is None and b.real is None
        print("  disable        -> speakers restored, state file removed")
    _isolated(go)


# -- supervision ------------------------------------------------------------

def test_check_is_quiet_when_nothing_changed():
    def go(state):
        core = FakeCoreAudio()
        b = backend(core)
        b.enable()
        before = len(core.switches)
        assert b.check() is None
        assert len(core.switches) == before, "re-asserted a default it already held"
        print("  steady state   -> no event, no redundant switching")
    _isolated(go)


def test_check_reasserts_a_stolen_default_immediately():
    """The filter must never sit bypassed, even for the debounce window --
    re-assert on the first tick, report the change later."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET])
        b = backend(core)
        b.enable()
        core.default = HEADSET              # a headset reconnects and grabs it
        event = b.check()
        assert event is None, f"reported {event!r} before the debounce elapsed"
        assert core.default == CABLE, "left the filter bypassed"
        print("  default stolen -> trap re-asserted at once, event debounced")
    _isolated(go)


def test_check_reports_a_change_once_it_holds():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET])
        b = backend(core)
        b.enable()
        core.default = HEADSET
        assert b.check() is None
        assert core.default == CABLE        # put back, so never seen twice
        assert b.settling
        windows.time.advance(WindowsBackend.RETARGET_DEBOUNCE_S + 0.1)
        assert b.check() == "real_sink_changed"
        assert b.real.name == HEADSET
        assert core.default == CABLE
        assert not b.settling
        print("  change holds   -> real_sink_changed, output follows")
    _isolated(go)


def test_a_single_pick_is_followed_without_a_second_sighting():
    """Windows' picker sets the default once and the trap goes straight
    back, so the candidate is confirmed by age, not by being seen again."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET])
        b = backend(core)
        b.enable()
        core.default = HEADSET
        assert b.check() is None            # first sighting, re-asserted
        windows.time.advance(WindowsBackend.RETARGET_DEBOUNCE_S - 0.5)
        assert b.check() is None and b.settling, "followed before it aged"
        windows.time.advance(1.0)
        assert b.check() == "real_sink_changed"
        print("  single pick    -> followed once it has aged")
    _isolated(go)


def test_a_pick_that_vanishes_or_repeats_is_dropped():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET])
        b = backend(core)
        b.enable()
        core.default = HEADSET
        b.check()
        core.present.remove(HEADSET)
        windows.time.advance(WindowsBackend.RETARGET_DEBOUNCE_S + 0.1)
        assert b.check() is None and b.real.name == SPEAKERS
        assert not b.settling
        core.present.append(HEADSET)
        core.default = SPEAKERS             # re-picking what we already use
        b.check()
        windows.time.advance(WindowsBackend.RETARGET_DEBOUNCE_S + 0.1)
        assert b.check() is None
        print("  transient / same device -> nothing followed")
    _isolated(go)


def test_check_ignores_a_flapping_device():
    """Each restart is an audible glitch, so a device that cannot hold the
    default for the debounce window must never cause one."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET, HDMI])
        b = backend(core)
        b.enable()
        for _ in range(6):
            core.default = HEADSET
            assert b.check() is None
            windows.time.advance(1.0)
            core.default = HDMI             # a different candidate each time
            assert b.check() is None
            windows.time.advance(1.0)
        assert b.real.name == SPEAKERS, f"retargeted to {b.real}"
        print("  flapping       -> never reported, output unchanged")
    _isolated(go)


def test_check_does_not_overwrite_the_users_choice():
    """Windows' own output picker changing the default is not the same as
    the user choosing an output in this app, and ui/app.py persists
    preferred_name -- so writing it here would silently rewrite config."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET])
        b = backend(core)
        b.preferred_name = SPEAKERS
        b.enable()
        core.default = HEADSET
        b.check()
        windows.time.advance(WindowsBackend.RETARGET_DEBOUNCE_S + 0.1)
        core.default = HEADSET
        assert b.check() == "real_sink_changed"
        assert b.preferred_name == SPEAKERS, \
            f"check() rewrote preferred_name to {b.preferred_name}"
        print("  system default -> followed, but preferred_name left alone")
    _isolated(go)


def test_check_reports_a_vanished_cable():
    """Uninstalling or disabling the cable mid-session. Distinct from losing
    an output device: nothing can be re-asserted and there is nothing left to
    capture from, so the session has to be rebuilt or stopped."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS])
        b = backend(core)
        b.enable()
        core.present.remove(CABLE)
        core.default = SPEAKERS
        assert b.check() == "trap_lost"
        assert b.trap is None
        print("  cable removed  -> trap_lost, not a device event")
    _isolated(go)


def test_check_replaces_a_vanished_output():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HDMI], default=SPEAKERS)
        b = backend(core)
        b.enable()
        core.present.remove(SPEAKERS)       # unplugged
        # 'replaced', not 'changed': their hardware went away, they did not
        # choose anything, and callers must not remember this as a choice.
        assert b.check() == "real_sink_replaced"
        assert b.real.name == HDMI
        print("  output unplugged-> real_sink_replaced, not a user choice")
    _isolated(go)


def test_check_reports_no_output_left():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=SPEAKERS)
        b = backend(core)
        b.enable()
        core.present.remove(SPEAKERS)
        assert b.check() == "real_sink_lost"
        assert b.real is None
        print("  nothing left   -> real_sink_lost")
    _isolated(go)


def test_check_only_ever_returns_documented_events():
    """base.py documents exactly these and both callers switch on the
    string, so an undocumented one is silently ignored -- the failure mode
    is the UI showing "on" with no audio flowing."""
    allowed = {None, "real_sink_changed", "real_sink_replaced",
               "real_sink_lost", "trap_lost"}
    seen = set()

    def go(state):
        for present, default, drop in (
            ([CABLE, SPEAKERS], SPEAKERS, None),
            ([CABLE, SPEAKERS, HEADSET], HEADSET, None),
            ([CABLE, SPEAKERS], SPEAKERS, CABLE),
            ([CABLE, SPEAKERS, HDMI], SPEAKERS, SPEAKERS),
            ([CABLE, SPEAKERS], SPEAKERS, SPEAKERS),
        ):
            core = FakeCoreAudio(present=list(present), default=default)
            b = backend(core)
            b.enable()
            if drop and drop in core.present:
                core.present.remove(drop)
            for _ in range(3):
                seen.add(b.check())
                windows.time.advance(1.0)
        # also the case where enable() never ran
        seen.add(backend().check())
    _isolated(go)
    assert seen <= allowed, f"undocumented events: {seen - allowed}"
    print(f"  event vocabulary-> {sorted(str(s) for s in seen)} all documented")


# -- crash recovery ---------------------------------------------------------

def test_recover_stale_restores_after_a_crash():
    def go(state):
        state.write_text(json.dumps({"previous_default": SPEAKERS, "ts": 1}))
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=CABLE)
        WindowsBackend.recover_stale(core)
        assert core.default == SPEAKERS, f"did not restore: {core.default}"
        assert not state.exists(), "left the state file in place"
        print("  after a crash  -> speakers restored, state file consumed")
    _isolated(go)


def test_recover_stale_defers_to_a_newer_user_choice():
    """If the default is no longer the cable, the user has picked something
    since the crash -- that is newer information than our state file."""
    def go(state):
        state.write_text(json.dumps({"previous_default": SPEAKERS, "ts": 1}))
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET], default=HEADSET)
        WindowsBackend.recover_stale(core)
        assert core.default == HEADSET, "overrode a choice made after the crash"
        assert core.switches == [], "switched anyway"
        assert not state.exists()
        print("  user moved on  -> recovery defers, file still consumed")
    _isolated(go)


def test_recover_stale_rescues_when_the_recorded_device_is_gone():
    """A missing previous device is not a reason to leave the machine mute.

    This test used to assert the opposite -- that recovery makes "no
    pointless switch attempt" and gives up. It is not pointless: the state
    file existing at all means we died mid-session, and a cable is holding
    the default, so giving up is how a user ends up with no audio, nothing
    on screen, and no idea that Sound settings is where to look. Any real
    output beats that, even one they did not choose.
    """
    def go(state):
        state.write_text(json.dumps({"previous_default": HEADSET, "ts": 1}))
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=CABLE)
        WindowsBackend.recover_stale(core)
        assert core.default == SPEAKERS, (
            f"left the cable holding the default: {core.default}")
        print("  device gone    -> falls back to a real output anyway")
    _isolated(go)


# -- a cable is never a speaker ---------------------------------------------
# Regression tests for the fault that cost a user their audio: "CABLE In
# 16ch" classed as hardware, so it could be rendered into and, worse, handed
# the system default on the way out. See VIRTUAL_RENDER_NAMES.

def test_a_second_cable_endpoint_is_not_offered_as_hardware():
    core = FakeCoreAudio(present=[CABLE, CABLE16, SPEAKERS], default=SPEAKERS)
    names = [s.name for s in backend(core)._sinks()]
    assert names == [SPEAKERS], f"offered a cable as an output: {names}"
    print("  16ch endpoint  -> excluded from the output list")


def test_enable_never_renders_into_a_cable():
    """The trap already holds the default and a cable enumerates first.

    Both fallbacks in _pick_real_in() are exercised: previous is the trap so
    it is discarded, and the first endpoint in enumeration order is the
    other cable.
    """
    def go(state):
        core = FakeCoreAudio(present=[CABLE16, CABLE, SPEAKERS], default=CABLE)
        real = backend(core).enable()
        assert real is not None, "refused to start with speakers present"
        assert real.name == SPEAKERS, f"chose {FRIENDLY[real.name]}"
        print("  destination    -> speakers, not the first cable in the list")
    _isolated(go)


def test_disable_never_hands_the_default_to_a_cable():
    """The output device vanishes mid-session, which is the path that bit.

    self.real is None here because that is what check() leaves behind when
    it reports real_sink_lost -- unplug the only speaker and ui/app.py turns
    off, landing straight in disable() with nothing recorded to go back to.
    """
    def go(state):
        core = FakeCoreAudio(present=[CABLE16, CABLE, SPEAKERS, HEADSET],
                             default=SPEAKERS)
        b = backend(core)
        b.enable()
        core.present.remove(SPEAKERS)       # the device we were playing to
        b.real = None
        b.disable()
        assert core.default == HEADSET, (
            f"handed the default to {FRIENDLY.get(core.default)}")
        assert CABLE16 not in core.switches, "tried the 16ch cable"
        print("  output lost    -> default goes to the headset, not a cable")
    _isolated(go)


def test_disable_leaves_the_default_alone_rather_than_pick_a_cable():
    """With no hardware left there is nothing good to do, so do nothing.

    Setting a cable here would report success and play nothing. Leaving the
    trap in place is equally silent but at least stays honest, and the next
    enable() or recover_stale() can still fix it once a device returns.
    """
    def go(state):
        core = FakeCoreAudio(present=[CABLE16, CABLE, SPEAKERS],
                             default=SPEAKERS)
        b = backend(core)
        b.enable()
        core.present.remove(SPEAKERS)
        b.real = None
        b.disable()
        assert core.switches == [CABLE], f"switched again: {core.switches}"
        print("  no hardware    -> no cable handed the default")
    _isolated(go)


def test_trap_render_present_is_what_the_installer_asks():
    """One source of truth for "is a cable installed".

    The installer used to answer this itself by testing for VB-Audio's
    registry key, and skipped the driver step on it. VB-CABLE's Remove
    Driver leaves that key behind, so setup said "already installed,
    skipping" while the app said "no virtual audio cable is installed" --
    on the same machine, minutes apart. The installer now shells out to
    --check-cable, which lands here.
    """
    assert windows.trap_render_present(
        FakeCoreAudio(present=[CABLE, SPEAKERS])) is True
    assert windows.trap_render_present(
        FakeCoreAudio(present=[SPEAKERS, HEADSET])) is False
    # The one that matters, and the reason this takes the trap list rather
    # than the virtual list: 16ch is a cable, but it is not one we can trap
    # through -- there is no CABLE Out 16ch capture side we read. A machine
    # with only that endpoint has no usable cable.
    assert windows.trap_render_present(
        FakeCoreAudio(present=[CABLE16, SPEAKERS])) is False
    print("  check-cable    -> endpoint-based, and 16ch alone is not a cable")


def test_the_platform_layer_dispatches_the_cable_check():
    """routing.py is the only place a platform is chosen; an installer flag
    that bypassed it would be a second platform branch."""
    from assassin_live.audio import routing
    assert callable(routing.trap_render_present)
    if sys.platform.startswith("linux"):
        assert routing.trap_render_present() is None, (
            "Linux needs no cable; None means 'does not apply', and False "
            "would read as 'go install a driver'")
    print("  dispatch       -> routing.trap_render_present, None on Linux")


def test_recover_stale_rescues_a_default_left_on_a_second_cable():
    """The machine a previous build could leave behind.

    Before VIRTUAL_RENDER_NAMES, disable() could make "CABLE In 16ch" the
    default and call that a restore. Recovery has to treat any cable holding
    the default as the thing it is there to undo, not just the trap.
    """
    def go(state):
        state.write_text(json.dumps({"previous_default": SPEAKERS, "ts": 1}))
        core = FakeCoreAudio(present=[CABLE16, CABLE, SPEAKERS],
                             default=CABLE16)
        WindowsBackend.recover_stale(core)
        assert core.default == SPEAKERS, f"left it on {core.default}"
        print("  stale 16ch     -> recovered, not just the trap")
    _isolated(go)


def test_recover_stale_survives_a_corrupt_state_file():
    def go(state):
        state.write_text("{not json at all")
        core = FakeCoreAudio(default=CABLE)
        WindowsBackend.recover_stale(core)      # must not raise
        assert not state.exists(), "left a corrupt file to fail again next time"
        print("  corrupt state  -> ignored and cleared, no crash")
    _isolated(go)


def test_enable_recovers_before_trapping():
    """enable() runs recovery first, so a crashed previous run cannot make
    the *new* run record the cable as the thing to restore to."""
    def go(state):
        state.write_text(json.dumps({"previous_default": SPEAKERS, "ts": 1}))
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=CABLE)
        b = backend(core)
        real = b.enable()
        assert real is not None and real.name == SPEAKERS, real
        assert json.loads(state.read_text())["previous_default"] == SPEAKERS, \
            "recorded the cable as the previous default"
        print("  stale on start -> recovered first, then trapped cleanly")
    _isolated(go)


# -- device lists and the engine seam ---------------------------------------

def test_list_sinks_hides_the_cable():
    """ui/app.py builds its output picker from this; offering the cable
    would let the user route the filter into its own input."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET])
        names = [s.name for s in backend(core)._sinks()]
        assert CABLE not in names, "the cable is offered as an output"
        assert names == [SPEAKERS, HEADSET]
        descs = [s.description for s in backend(core)._sinks()]
        assert descs == [FRIENDLY[SPEAKERS], FRIENDLY[HEADSET]], descs
        print("  list_sinks     -> real outputs only, friendly names kept")
    _isolated(go)


def test_sink_names_are_stable_endpoint_ids():
    """ui/app.py persists SinkInfo.name across restarts and matches on it,
    so it must be the endpoint id -- friendly names are duplicated freely
    (one test machine reported six endpoints all called "Digital Audio
    (HDMI)")."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS])
        s = backend(core)._sinks()[0]
        assert s.name.startswith("{0.0.0."), f"name is not an endpoint id: {s.name}"
        assert s.name != s.description
        print("  sink identity  -> endpoint id in .name, label in .description")
    _isolated(go)


class FakeSounddevice:
    """Just enough of sounddevice for resolve_stream_devices()."""

    def __init__(self, devices, hostapis=("MME", "Windows WASAPI")):
        self._devices = devices
        self._hostapis = [{"name": n} for n in hostapis]

    def query_hostapis(self):
        return self._hostapis

    def query_devices(self):
        return self._devices


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


def test_resolve_prefers_wasapi():
    """Every device appears once per host API. MME truncates names to 31
    characters and resamples; DirectSound adds buffering on top of our
    latency budget. So WASAPI, when it is there."""
    devices = [
        {"name": "CABLE Output (VB-Audio Virtual ", "hostapi": 0,
         "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Speakers (Realtek(R) Audio)", "hostapi": 0,
         "max_input_channels": 0, "max_output_channels": 2},
        {"name": "CABLE Output (VB-Audio Virtual Cable)", "hostapi": 1,
         "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Speakers (Realtek(R) Audio)", "hostapi": 1,
         "max_input_channels": 0, "max_output_channels": 2},
    ]

    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS])
        b = backend(core)
        capture, playback = _with_sounddevice(
            FakeSounddevice(devices),
            lambda: b.resolve_stream_devices(b.monitor_source, SPEAKERS))
        assert (capture, playback) == (2, 3), (capture, playback)
        print("  resolve        -> WASAPI indices, not the MME duplicates")
    _isolated(go)


def test_resolve_refuses_rather_than_feeding_back():
    """Returning PortAudio's default here would render into our own input:
    while the filter is on, the default IS the cable."""
    devices = [
        {"name": "Speakers (Realtek(R) Audio)", "hostapi": 1,
         "max_input_channels": 0, "max_output_channels": 2},
    ]

    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS])
        b = backend(core)
        try:
            _with_sounddevice(
                FakeSounddevice(devices),
                lambda: b.resolve_stream_devices(b.monitor_source, SPEAKERS))
        except RuntimeError as e:
            assert "cable" in str(e).lower(), str(e)
            print("  no capture dev -> RuntimeError naming the cable")
            return
        raise AssertionError("fell back to a default instead of refusing")
    _isolated(go)


def test_resolve_refuses_an_absent_output():
    devices = [
        {"name": "CABLE Output (VB-Audio Virtual Cable)", "hostapi": 1,
         "max_input_channels": 2, "max_output_channels": 0},
    ]

    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS])
        b = backend(core)
        try:
            _with_sounddevice(
                FakeSounddevice(devices),
                lambda: b.resolve_stream_devices(b.monitor_source, SPEAKERS))
        except RuntimeError as e:
            assert "output" in str(e).lower(), str(e)
            print("  no output dev  -> RuntimeError rather than a wrong device")
            return
        raise AssertionError("opened a stream with no matching output")
    _isolated(go)


def test_resolve_works_without_wasapi():
    """A machine with no WASAPI host API should still run, just worse."""
    devices = [
        {"name": "CABLE Output (VB-Audio Virtual ", "hostapi": 0,
         "max_input_channels": 2, "max_output_channels": 0},
        {"name": "Speakers (Realtek(R) Audio)", "hostapi": 0,
         "max_input_channels": 0, "max_output_channels": 2},
    ]

    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS])
        b = backend(core)
        capture, playback = _with_sounddevice(
            FakeSounddevice(devices, hostapis=("MME",)),
            lambda: b.resolve_stream_devices(b.monitor_source, SPEAKERS))
        assert (capture, playback) == (0, 1)
        print("  no WASAPI      -> falls back to another host API")
    _isolated(go)


def test_disable_restores_even_with_no_known_output():
    """The unplug case. check() reported real_sink_lost, ui/app.py turned
    off, and self.real is None -- but the cable is a driver that stays
    installed, so returning without switching would leave it holding the
    default and the machine silent. Linux self-heals here (destroying the
    trap node lets WirePlumber choose); Windows does not."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=SPEAKERS)
        b = backend(core)
        b.enable()
        core.present.remove(SPEAKERS)           # the only output goes away
        assert b.check() == "real_sink_lost"
        assert b.real is None
        core.present.append(HEADSET)            # something else turns up
        b.disable()
        assert core.default == HEADSET, \
            f"left the cable holding the default ({core.default})"
        print("  disable w/o real-> still hands the default to a real device")
    _isolated(go)


def test_disable_falls_back_to_the_recorded_default():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET], default=SPEAKERS)
        b = backend(core)
        b.enable()
        b.real = None                           # as after real_sink_lost
        b.disable()
        assert core.default == SPEAKERS, \
            f"ignored the recorded previous default ({core.default})"
        print("  disable fallback-> uses the default recorded before the swap")
    _isolated(go)


def test_disable_never_targets_the_cable():
    def go(state):
        core = FakeCoreAudio(present=[CABLE], default=CABLE)
        b = backend(core)
        b.trap = b._sink_for(CABLE)
        b.real = None
        b.disable()                             # nothing to restore to
        assert CABLE not in core.switches, "tried to restore onto the cable"
        print("  nothing to use  -> declines rather than re-trapping")
    _isolated(go)


def test_check_enumerates_once_per_tick():
    """check() runs about once a second on the UI thread and each
    enumeration is a COM round trip over every endpoint on the machine -- 40
    of them on one test box. It also has to be consistent: two enumerations
    in one tick can disagree, and then the device picked is not the device
    checked."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS, HEADSET, HDMI])
        b = backend(core)
        b.enable()
        core.enumerations = 0
        b.check()
        assert core.enumerations == 1, \
            f"{core.enumerations} enumerations in one quiet tick"
        # and in the branch that has the most work to do
        core.default = HEADSET
        core.enumerations = 0
        b.check()
        assert core.enumerations == 1, \
            f"{core.enumerations} enumerations while re-asserting the trap"
        print("  check cost     -> exactly one enumeration per tick")
    _isolated(go)


def test_matches_tolerates_mme_name_truncation():
    """MME cuts device names to 31 characters, so a full Core Audio friendly
    name will not be found inside PortAudio's MME spelling of it. Only
    reachable on the no-WASAPI fallback path, which is exactly where it
    would otherwise fail silently."""
    full = "Speakers (NVIDIA High Definition Audio)"
    truncated = full[:31]
    assert len(truncated) == 31
    assert windows._matches(truncated, [full]), \
        "a truncated device name did not match its full friendly name"
    assert windows._matches(full, ["Speakers (NVIDIA"]), "substring broke"
    # and it must not start matching unrelated devices
    assert not windows._matches("Headphones (Realtek)", [full])
    assert not windows._matches("", [full])
    assert not windows._matches("Speakers", [full]), \
        "a short name matched a long one -- too loose to tell devices apart"
    print("  name matching  -> truncation-tolerant, still discriminating")


def test_pin_stream_is_a_noop_that_succeeds():
    """engine.py prints a warning on False. resolve_stream_devices() names
    real PortAudio devices here, so there is nothing to pin and no warning
    to emit."""
    def go(state):
        assert backend().pin_stream(1234, "CABLE Output", SPEAKERS) is True
        print("  pin_stream     -> True, no spurious warning from the engine")
    _isolated(go)


# -- volume mirror (C2) -----------------------------------------------------

def test_adopt_volume_matches_the_cable_to_the_speakers():
    """Inserting the filter must not change how loud anything is, nor make
    the system slider jump."""
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        core.volumes[SPEAKERS] = (0.3, False)
        core.volumes[CABLE] = (1.0, False)
        b = backend(core)
        b.enable()
        b.adopt_volume()
        assert core.volumes[CABLE] == (0.3, False), core.volumes[CABLE]
        print("  adopt_volume   -> cable seeded from the speakers' level")
    _isolated(go)


def test_sync_volume_makes_the_volume_keys_work():
    """While filtering, the cable IS the system default, so the keys move it
    and its render side goes nowhere. Without the mirror they are inert."""
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        b = backend(core)
        b.enable()
        b.adopt_volume()
        assert b.sync_volume() is None, "mirrored without a change"
        core.volumes[CABLE] = (0.2, False)        # user presses volume-down
        assert b.sync_volume() == (0.2, False)
        assert core.volumes[SPEAKERS] == (0.2, False), core.volumes[SPEAKERS]
        print("  sync_volume    -> cable's level copied onto the speakers")
    _isolated(go)


def test_sync_volume_mirrors_mute():
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        b = backend(core)
        b.enable(); b.adopt_volume()
        core.volumes[CABLE] = (0.5, True)
        assert b.sync_volume() == (0.5, True)
        assert core.volumes[SPEAKERS][1] is True
        print("  mute           -> mirrored as well as the level")
    _isolated(go)


def test_sync_volume_only_acts_on_a_change():
    """So adjusting the real device directly in a mixer is not immediately
    overwritten on the next tick."""
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        b = backend(core)
        b.enable(); b.adopt_volume()
        core.volumes[SPEAKERS] = (0.9, False)     # changed in a mixer
        for _ in range(4):
            assert b.sync_volume() is None
        assert core.volumes[SPEAKERS] == (0.9, False), "overwrote the mixer"
        print("  no change      -> real device left alone")
    _isolated(go)


def test_first_reading_is_a_baseline_not_a_change():
    """Without adopt_volume() having run, the first observation must not be
    mirrored -- there is nothing to compare it against yet."""
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        core.volumes[SPEAKERS] = (0.4, False)
        core.volumes[CABLE] = (0.8, False)
        b = backend(core)
        b.enable()
        assert b.sync_volume() is None, "mirrored a baseline reading"
        assert core.volumes[SPEAKERS] == (0.4, False)
        core.volumes[CABLE] = (0.6, False)
        assert b.sync_volume() == (0.6, False)
        print("  baseline       -> first reading recorded, not applied")
    _isolated(go)


def test_volume_methods_survive_no_volume_interface():
    """IAudioEndpointVolume can fail to activate. That must degrade, not
    raise: this runs on the same ~1 s tick as check()."""
    def go(state):
        core = FakeCoreAudio(default=SPEAKERS)
        b = backend(core)
        b.enable()
        core.deaf = True
        b.adopt_volume()                 # must not raise
        assert b.sync_volume() is None
        print("  no volume iface-> degrades quietly, does not raise")
    _isolated(go)


def test_volume_methods_are_inert_before_enable():
    def go(state):
        b = backend()
        b.adopt_volume()
        assert b.sync_volume() is None
        print("  before enable  -> volume mirror is inert")
    _isolated(go)


# -- the other two seam methods --------------------------------------------

def test_retarget_playback_declines():
    """A WASAPI stream is bound to its endpoint when it opens and neither
    PortAudio nor sounddevice can move a running one. base.py's documented
    answer is False, meaning "use the stop/start path"."""
    def go(state):
        assert backend().retarget_playback(1234, SPEAKERS) is False
        print("  retarget_play  -> False, so callers use stop/start")
    _isolated(go)


def test_diagnose_capture_is_quiet_when_correct():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=SPEAKERS)
        b = backend(core)
        b.enable()
        assert b.diagnose_capture(1234) is None
        print("  diagnose ok    -> None when routing is sound")
    _isolated(go)


def test_diagnose_capture_sees_a_vanished_cable():
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=SPEAKERS)
        b = backend(core)
        b.enable()
        core.present.remove(CABLE)
        assert b.diagnose_capture(1234) == "trap_lost"
        print("  diagnose       -> trap_lost when the cable goes")
    _isolated(go)


def test_diagnose_capture_catches_a_feedback_loop():
    """Rendering into the cable we capture from: output feeds input. On
    Windows this is reachable, because a cable's render endpoint is an
    ordinary output that could be selected."""
    def go(state):
        core = FakeCoreAudio(present=[CABLE, SPEAKERS], default=SPEAKERS)
        b = backend(core)
        b.enable()
        b.real = b._sink_for(CABLE)          # the pathological case
        assert b.diagnose_capture(1234) == "feedback_loop"
        print("  diagnose       -> feedback_loop when we play into the cable")
    _isolated(go)


def test_diagnose_capture_is_inert_before_enable():
    def go(state):
        assert backend().diagnose_capture(1234) is None
        print("  diagnose       -> None before a session exists")
    _isolated(go)


def test_implements_the_newer_protocol_too():
    """The Protocol grew sync_volume, adopt_volume, retarget_playback and
    diagnose_capture (and check() two outcomes), then settling and relabel.
    Asserted by name so a backend cannot quietly miss part of the interface."""
    for name in ("sync_volume", "adopt_volume", "retarget_playback",
                 "diagnose_capture", "relabel"):
        assert callable(getattr(WindowsBackend, name, None)), \
            f"WindowsBackend is missing {name}"
    assert isinstance(WindowsBackend.settling, property)
    b = backend()
    assert b.settling is False and b.relabel(None) is False
    print("  newer protocol -> every method present; settling/relabel inert")


def main():
    print("\nwindows routing backend")
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS")


if __name__ == "__main__":
    main()
