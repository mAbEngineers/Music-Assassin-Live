"""WASAPI virtual-cable routing backend (Windows).

Mirrors what backends/pipewire.py does on Linux, by a different mechanism:
make a virtual output device the system default so every default-following
app plays into it, capture that device's other end, and render the processed
result to the real hardware endpoint. Disable restores everything.

WHY A VIRTUAL CABLE IS REQUIRED. WASAPI loopback is a tap, not an insert:
capturing a real device's loopback leaves the original audio still playing,
so the processed signal would be added on top of the music rather than
substituted for it -- the opposite of what this app is for. (sounddevice
exposes no loopback flag anyway; checked on 0.5.5 and 0.5.6, Windows 10 and
Server 2025.) The only user-mode way to sit *in* the path is a virtual
device, so v1 requires one to be installed. The alternative -- an APO, which
can insert properly -- is a C++ driver component, not something reachable
from here.

    apps -> [CABLE Input]  (we make this the system default)
                 |
            CABLE Output   (a real capture endpoint; the engine reads it)
                 |
            processing
                 |
            the real speakers (the engine renders here)

CABLE Output is an ordinary capture endpoint, so no loopback is needed
anywhere. Unlike PipeWire there is also no trap to *create* or tear down:
the cable is a driver the user installed, and all we ever change is which
endpoint is default.

THE ONE UNDOCUMENTED PART. Windows exposes no public API for setting the
default endpoint. IPolicyConfig does it, and it is undocumented, with a
CLSID/IID that has shifted across Windows versions. That was the risk worth
retiring before writing any of this, and it was: on Windows 10 (10.0.19044)
and Server 2025 (10.0.26100) the interface instantiates and
SetDefaultEndpoint succeeds for all three roles. See
scripts/spike_d3_windows_routing.py, the source of the vtable layout below.

Implements the RoutingBackend seam (backends/base.py); routing.py selects it
as RoutingSession on win32.
"""

import json
import sys
import time

from ...paths import ROUTING_STATE
from .base import SinkInfo

# The virtual cables we can trap through. VB-CABLE is what the installer
# steers people to (packaging/windows/README.md); the others are listed
# because someone may already have one, and any of them works -- all this
# needs is a render endpoint whose capture side can be read back.
# Matched case-insensitively as substrings of endpoint friendly names.
TRAP_RENDER_NAMES = (
    "CABLE Input",          # VB-CABLE
    "VoiceMeeter Input",    # VoiceMeeter, same vendor
    "Virtual Audio Cable",  # VAC
)
TRAP_CAPTURE_NAMES = (
    "CABLE Output",
    "VoiceMeeter Output",
    "Virtual Audio Cable",
)

# Named for symmetry with pipewire.py, which ui/app.py imports to filter the
# trap out of the output picker. The filter is a no-op here -- list_sinks()
# already excludes the trap, and SinkInfo.name carries an endpoint id rather
# than a friendly name -- but the import has to resolve on both platforms.
SINK_NAME = "CABLE Input"
SINK_DESC = "VB-CABLE (Music Assassin trap)"

# IPolicyConfig, as validated by the spike. Two variants because the Vista
# one is still registered on some systems and costs nothing to try.
_POLICY_CONFIG_VARIANTS = (
    ("{870af99c-171d-4f9e-af0d-e63df40c2bc9}",
     "{f8679f50-850a-41cf-9c72-430f290290c8}"),
    ("{294935CE-F637-4E7C-A41B-AB255460B862}",
     "{568b9108-44bf-40b4-9006-86afe5b5a620}"),
)

# eConsole, eMultimedia, eCommunications. All three, because Windows keeps a
# separate default per role, and leaving one behind means some apps keep
# playing straight to the speakers while the filter is on.
_ROLES = (0, 1, 2)

# The MME host API truncates device names to 31 characters. Only used to
# decide whether a short name is plausibly a truncation rather than a
# different device -- see _matches().
_MME_NAME_LIMIT = 31

# The ten methods preceding SetDefaultEndpoint in the vtable, declared as
# empty placeholders purely to put it at the right offset. They are never
# called; declaring their real signatures would be a lot of WAVEFORMATEX for
# no benefit. The spike confirmed this layout by making the call and getting
# S_OK for all three roles.
_POLICY_CONFIG_PLACEHOLDERS = (
    "GetMixFormat", "GetDeviceFormat", "ResetDeviceFormat", "SetDeviceFormat",
    "GetProcessingPeriod", "SetProcessingPeriod", "GetShareMode",
    "SetShareMode", "GetPropertyValue", "SetPropertyValue",
)


def _matches(friendly: str, candidates) -> bool:
    """Is this device name one of the ones we are looking for?

    Substring, case-insensitive, and tolerant of truncation in the *device*
    name: the MME host API cuts names to 31 characters, so a full friendly
    name from Core Audio ("Speakers (NVIDIA High Definition Audio)") will not
    be found inside PortAudio's MME spelling of it. Comparing the other way
    round as well costs nothing and keeps the no-WASAPI fallback path in
    resolve_stream_devices() working.
    """
    low = (friendly or "").lower()
    if not low:
        return False
    for c in candidates:
        c = (c or "").lower()
        if not c:
            continue
        if c in low:
            return True
        # device name truncated: compare on the shorter of the two
        if len(low) < len(c) and len(low) >= _MME_NAME_LIMIT and low in c:
            return True
    return False


class CoreAudio:
    """Everything this backend needs from Windows Core Audio, and no more.

    Narrow on purpose. This is the only code here that cannot run or be
    tested off Windows, so it holds no policy and makes no decisions: each
    method answers one question or performs one action. WindowsBackend takes
    one of these as a parameter, so the state machine above it -- which is
    where the interesting failures live -- is testable on any platform with
    a fake.

    Every method degrades to None/[]/False rather than raising. A backend
    that throws while putting the user's audio device back is worse than one
    that reports it could not.
    """

    def __init__(self):
        self._policy = None
        self._policy_tried = False

    def render_endpoints(self) -> list:
        """Active render endpoints, as (endpoint_id, friendly_name)."""
        try:
            from pycaw.pycaw import AudioUtilities
            enumerator = AudioUtilities.GetDeviceEnumerator()
            # eRender = 0, DEVICE_STATE_ACTIVE = 0x1 -- numeric rather than
            # pycaw's enums, which have moved between modules across versions.
            collection = enumerator.EnumAudioEndpoints(0, 1)
            out = []
            for i in range(collection.GetCount()):
                try:
                    dev = AudioUtilities.CreateDevice(collection.Item(i))
                except Exception:
                    continue            # one unreadable endpoint is not fatal
                if dev is not None and dev.id:
                    out.append((dev.id, dev.FriendlyName or dev.id))
            return out
        except Exception:
            return []

    def default_render_endpoint(self) -> "str | None":
        """The endpoint id currently serving as the eConsole default."""
        try:
            from pycaw.pycaw import AudioUtilities
            dev = AudioUtilities.GetSpeakers()
        except Exception:
            return None
        # pycaw has returned two different things here across versions: a raw
        # IMMDevice (GetId()) and an AudioDevice wrapper (.id). Assuming the
        # first cost a whole round trip to an offline test machine once, so
        # try both.
        for attr in ("id", "GetId"):
            try:
                value = getattr(dev, attr)
                value = value() if callable(value) else value
            except Exception:
                continue
            if isinstance(value, str) and value:
                return value
        return None

    def _policy_config(self):
        if self._policy_tried:
            return self._policy
        self._policy_tried = True
        try:
            from ctypes import c_int, c_wchar_p

            from comtypes import (COMMETHOD, GUID, HRESULT, CoCreateInstance,
                                  IUnknown)
        except Exception:
            return None
        for clsid, iid in _POLICY_CONFIG_VARIANTS:
            try:
                methods = [COMMETHOD([], HRESULT, name)
                           for name in _POLICY_CONFIG_PLACEHOLDERS]
                methods.append(COMMETHOD(
                    [], HRESULT, "SetDefaultEndpoint",
                    (["in"], c_wchar_p, "deviceId"),
                    (["in"], c_int, "role")))
                iface = type("IPolicyConfig", (IUnknown,),
                             {"_iid_": GUID(iid), "_methods_": methods})
                self._policy = CoCreateInstance(GUID(clsid), interface=iface)
                return self._policy
            except Exception:
                continue
        return None

    def set_default_render_endpoint(self, endpoint_id: str) -> bool:
        """Make endpoint_id the default for every role. True if any took."""
        policy = self._policy_config()
        if policy is None or not endpoint_id:
            return False
        ok = False
        for role in _ROLES:
            try:
                policy.SetDefaultEndpoint(endpoint_id, role)
                ok = True
            except Exception:
                continue        # one role refusing is not the whole switch
        return ok


class WindowsBackend:
    """enable() -> filter is inserted; disable() -> system restored.

    check() must be called periodically (~1 s) while enabled; it re-asserts
    the trap as default if something stole it, and reports when the real
    output endpoint changed or vanished.

    Satisfies RoutingBackend (backends/base.py) structurally.
    """

    # Same reasoning as PipeWireBackend.RETARGET_DEBOUNCE_S, and the same
    # need: a candidate endpoint must hold the default continuously for this
    # long before we act on it, or a flapping device (Bluetooth reconnect
    # loop, an HDMI monitor waking up) triggers a full engine restart every
    # tick, and each restart is an audible glitch.
    RETARGET_DEBOUNCE_S = 1.5

    def __init__(self, core: "CoreAudio | None" = None):
        self._core = core if core is not None else CoreAudio()
        self.trap: SinkInfo | None = None
        self.real: SinkInfo | None = None
        self.preferred_name: str | None = None  # user-picked endpoint, if any
        self._pending: SinkInfo | None = None
        self._pending_since: float = 0.0

    # -- availability ------------------------------------------------------
    @staticmethod
    def unsupported_reason() -> "str | None":
        """See RoutingBackend.unsupported_reason()."""
        if not sys.platform.startswith("win"):
            return ("The Windows backend only runs on Windows, not on "
                    f"{sys.platform}.")
        try:
            import comtypes         # noqa: F401
            import pycaw            # noqa: F401
        except Exception as e:      # noqa: BLE001
            return (
                "Music Assassin Live is missing its Windows audio "
                f"dependencies ({e}).\n\n"
                "Reinstall the app, or if you are running from source:\n"
                "    pip install pycaw comtypes")
        if not any(_matches(name, TRAP_RENDER_NAMES)
                   for _, name in CoreAudio().render_endpoints()):
            return (
                "No virtual audio cable is installed.\n\n"
                "Windows has no way to insert a filter into the audio path, "
                "so Music Assassin Live routes sound through a virtual cable "
                "instead. VB-CABLE is free:\n\n"
                "    https://vb-audio.com/Cable/\n\n"
                "Download the ZIP, extract it, right-click "
                "VBCABLE_Setup_x64.exe and Run as administrator, then "
                "reboot. This message will go away once it is installed.")
        return None

    # -- endpoint enumeration ---------------------------------------------
    @staticmethod
    def _to_sinks(endpoints, include_trap: bool = False) -> list:
        """Turn one already-fetched endpoint list into SinkInfo.

        Separate from _sinks() so a single operation can enumerate once and
        then ask several questions of the answer. That is partly about cost
        -- check() runs about once a second and each enumeration is a COM
        round trip over every endpoint on the machine, 40 of them on one
        test box -- and partly about consistency: two enumerations inside one
        tick can disagree, and then the device we picked is not the device we
        checked.
        """
        out = []
        for idx, (eid, friendly) in enumerate(endpoints):
            if not include_trap and _matches(friendly, TRAP_RENDER_NAMES):
                continue
            out.append(SinkInfo(id=idx, name=eid, description=friendly))
        return out

    def _sinks(self, include_trap: bool = False) -> list:
        """Render endpoints as SinkInfo.

        SinkInfo.id is an int nothing reads on this platform (ui/app.py
        matches on .name), so it is just the enumeration index. The *name*
        carries the Windows endpoint id, because that is the only stable
        handle: friendly names are duplicated freely -- one test machine
        reported six endpoints all called "Digital Audio (HDMI)" -- and
        ui/app.py persists this name as the user's output choice across
        restarts. The friendly name goes in description, which is what the
        UI displays.
        """
        return self._to_sinks(self._core.render_endpoints(), include_trap)

    @staticmethod
    def _find_trap(endpoints) -> "str | None":
        for eid, friendly in endpoints:
            if _matches(friendly, TRAP_RENDER_NAMES):
                return eid
        return None

    def _sink_for(self, endpoint_id: str) -> "SinkInfo | None":
        return self._sink_for_in(self._core.render_endpoints(), endpoint_id)

    @classmethod
    def _sink_for_in(cls, endpoints, endpoint_id: str) -> "SinkInfo | None":
        if not endpoint_id:
            return None
        for s in cls._to_sinks(endpoints, include_trap=True):
            if s.name == endpoint_id:
                return s
        return None

    @classmethod
    def _pick_real_in(cls, endpoints, preferred_name, fallback):
        """The endpoint to play to: the user's choice if it is still here,
        else the caller's fallback, else anything that is not the trap."""
        if preferred_name:
            for s in cls._to_sinks(endpoints):
                if s.name == preferred_name:
                    return s
        if fallback is not None:
            return fallback
        return next(iter(cls._to_sinks(endpoints)), None)

    # -- crash recovery ----------------------------------------------------
    @staticmethod
    def recover_stale(core: "CoreAudio | None" = None) -> None:
        """Put the default endpoint back if we died with the trap installed.

        The optional argument is why this still satisfies base.py's
        no-arguments staticmethod contract while enable() can hand it the
        instance's own CoreAudio -- otherwise it would build a second one
        and quietly bypass an injected fake, which is exactly the seam the
        tests use.

        Nothing to tear down, unlike PipeWire: the cable is the user's
        driver, not a node we created, so the only thing that can be stale
        is which endpoint is default -- which is also the part the user
        actually notices.
        """
        if not ROUTING_STATE.is_file():
            return
        try:
            previous = json.loads(
                ROUTING_STATE.read_text()).get("previous_default")
        except Exception:
            previous = None
        finally:
            ROUTING_STATE.unlink(missing_ok=True)
        if not previous:
            return
        core = core if core is not None else CoreAudio()
        endpoints = core.render_endpoints()
        trap = WindowsBackend._find_trap(endpoints)
        current = core.default_render_endpoint()
        # Only intervene while the trap is still holding the default. If the
        # user has picked something themselves since the crash, that is a
        # newer decision than anything in our state file.
        if trap and current == trap and any(eid == previous
                                            for eid, _ in endpoints):
            core.set_default_render_endpoint(previous)

    # -- lifecycle ---------------------------------------------------------
    def enable(self) -> "SinkInfo | None":
        """Make the cable the default endpoint. Returns the real output
        endpoint the engine should render to (None if there is no hardware
        output available -- caller should wait and retry)."""
        self.recover_stale(self._core)
        endpoints = self._core.render_endpoints()
        trap_id = self._find_trap(endpoints)
        if trap_id is None:
            return None             # no cable: unsupported_reason() explains
        trap = self._sink_for_in(endpoints, trap_id)

        previous = self._core.default_render_endpoint()
        if previous == trap_id:
            previous = None         # a stale trap default is not worth restoring
        real = self._pick_real_in(
            endpoints, self.preferred_name,
            self._sink_for_in(endpoints, previous))
        if real is None:
            return None             # cable installed but nothing to play to

        # Persisted *before* the swap, so a crash between the two leaves
        # recover_stale() something to work from.
        try:
            ROUTING_STATE.write_text(json.dumps(
                {"previous_default": previous or real.name, "ts": time.time()}))
        except Exception:
            pass        # arming recovery is a convenience, not a precondition
        if not self._core.set_default_render_endpoint(trap_id):
            ROUTING_STATE.unlink(missing_ok=True)
            return None
        self.trap = trap
        self.real = real
        self._pending = None
        self._pending_since = 0.0
        return real

    def disable(self) -> None:
        """Hand the default endpoint back to a real output device.

        The fallbacks matter more here than on Linux. self.real is None
        whenever check() last reported 'real_sink_lost' -- unplug your only
        speaker and ui/app.py turns off, which lands here -- and on Linux
        that self-heals, because destroying the trap node leaves WirePlumber
        to choose a new default. Nothing self-heals on Windows: the cable is
        a driver that stays installed, so returning without switching would
        leave it holding the default and the machine silent until the user
        found the sound settings themselves.
        """
        endpoints = self._core.render_endpoints()
        target = self.real.name if self.real is not None else None
        if target is None or not any(eid == target for eid, _ in endpoints):
            # Whatever we recorded before the swap, if it is still here.
            previous = None
            try:
                if ROUTING_STATE.is_file():
                    previous = json.loads(
                        ROUTING_STATE.read_text()).get("previous_default")
            except Exception:
                previous = None
            if previous and any(eid == previous for eid, _ in endpoints):
                target = previous
            else:
                # Anything at all, as long as it is not the cable.
                fallback = next(iter(self._to_sinks(endpoints)), None)
                target = fallback.name if fallback is not None else None
        if target:
            self._core.set_default_render_endpoint(target)
        self.trap = self.real = None
        self._pending = None
        ROUTING_STATE.unlink(missing_ok=True)

    # -- supervision -------------------------------------------------------
    def check(self) -> "str | None":
        """Returns None (all good), 'real_sink_changed', or 'real_sink_lost'.

        Only those three: both callers (__main__.py, ui/app.py) switch on
        the string, so anything else would be silently ignored. In
        particular a vanished cable reports 'real_sink_lost' -- routing is
        broken either way, and that is the event that makes the UI turn
        cleanly off instead of showing "on" with no audio flowing.
        """
        if self.trap is None:
            return None
        endpoints = self._core.render_endpoints()

        trap_id = self._find_trap(endpoints)
        if trap_id is None:
            # The cable was uninstalled or disabled under us. There is no
            # trap to re-assert and nothing to capture from.
            self.trap = None
            return "real_sink_lost"
        self.trap = self._sink_for_in(endpoints, trap_id) or self.trap

        default = self._core.default_render_endpoint()
        if default != trap_id:
            # Something took the default away from the trap: Windows' own
            # output picker, a device arriving and claiming it, an app.
            # Always re-assert immediately so the filter never sits
            # bypassed, but only report a device change -- which costs an
            # engine restart -- once the candidate has held steady.
            if default is None:
                self._core.set_default_render_endpoint(trap_id)
                return None
            candidate = self._sink_for_in(endpoints, default)
            if candidate is None or _matches(candidate.description,
                                             TRAP_RENDER_NAMES):
                self._core.set_default_render_endpoint(trap_id)
                return None
            if self._pending is None or candidate.name != self._pending.name:
                self._pending = candidate
                self._pending_since = time.monotonic()
            self._core.set_default_render_endpoint(trap_id)
            stable = (time.monotonic() - self._pending_since
                      >= self.RETARGET_DEBOUNCE_S)
            if stable and (self.real is None
                           or candidate.name != self.real.name):
                # Deliberately not recorded as preferred_name: the user
                # changing the system default is not the same as them
                # choosing an output in this app, and overwriting it here
                # would silently rewrite the choice ui/app.py persisted.
                self.real = candidate
                return "real_sink_changed"
            return None

        # The trap still holds the default; make sure our output endpoint is
        # still there.
        self._pending = None
        ids = {eid for eid, _ in endpoints}
        if self.real is not None and self.real.name not in ids:
            self.real = self._pick_real_in(endpoints, self.preferred_name, None)
            return "real_sink_changed" if self.real else "real_sink_lost"
        return None

    # -- engine seam -------------------------------------------------------
    @property
    def monitor_source(self) -> str:
        """What the engine captures from: the cable's capture side.

        No monitor to construct, unlike PipeWire -- the cable ships one as an
        ordinary capture endpoint. Returned as a friendly-name fragment
        because PortAudio matches on names, not endpoint ids.
        """
        return TRAP_CAPTURE_NAMES[0]

    def resolve_stream_devices(self, monitor_source: str, sink_name: str) -> tuple:
        """Map endpoints to the PortAudio indices sd.Stream(device=...) wants.

        Matched by name within the WASAPI host API, because every device
        appears once per host API and the others are worse for this: MME
        truncates names to 31 characters and resamples, DirectSound adds its
        own buffering on top of our latency budget.

        sink_name is a Windows endpoint id, which PortAudio has never heard
        of, so it is translated back to a friendly name first.
        """
        import sounddevice as sd

        wasapi = next((idx for idx, api in enumerate(sd.query_hostapis())
                       if "WASAPI" in api["name"]), None)
        devices = list(enumerate(sd.query_devices()))

        def find(names, want_input: bool):
            # WASAPI first, then any host API, so a machine with WASAPI
            # missing still works rather than refusing to start.
            for host in (wasapi, None):
                for idx, d in devices:
                    if host is not None and d["hostapi"] != host:
                        continue
                    channels = (d["max_input_channels"] if want_input
                                else d["max_output_channels"])
                    if channels > 0 and _matches(d["name"], names):
                        return idx
            return None

        target = self._sink_for(sink_name)
        playback_names = [target.description] if (
            target is not None and target.description) else []

        capture = find((monitor_source,) + TRAP_CAPTURE_NAMES, want_input=True)
        playback = find(playback_names, want_input=False) if playback_names \
            else None

        # Falling back to PortAudio's own default would be actively harmful
        # here: while we are enabled the default *is* the trap, so the
        # stream would render into its own input. Fail loudly instead of
        # opening a feedback loop. Both callers already treat a failed
        # start()/retarget() as "stop cleanly".
        if capture is None:
            raise RuntimeError(
                f"no capture device matching {monitor_source!r} -- is the "
                "virtual audio cable still installed and enabled?")
        if playback is None:
            raise RuntimeError(
                "no PortAudio output device matching "
                f"{playback_names or sink_name!r}")
        return capture, playback

    def pin_stream(self, pid: int, monitor_source: str, sink_name: str) -> bool:
        """Nothing to do: resolve_stream_devices() names real PortAudio
        devices directly, so there are no per-stream hints for a host API to
        ignore and no second pass to make. (This is the case base.py
        describes as a best-effort pass; on Windows the first pass is
        authoritative.)"""
        return True


def list_sinks() -> list:
    """Module-level form, for callers that ask before a session exists.
    Mirrors backends/pipewire.list_sinks()."""
    return WindowsBackend()._sinks()
