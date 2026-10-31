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
import threading
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


_com_state = threading.local()


def _ensure_com() -> None:
    """Initialise COM on the calling thread, once per thread.

    comtypes calls CoInitialize() for the thread that imports it and no
    other, so every COM call from a thread we spawned ourselves fails with
    "CoInitialize has not been called" until this runs. That is not a
    hypothetical: ui/app.py does enable()/disable() on a worker thread so the
    Tk loop keeps painting, which meant the startup cable check (main thread)
    succeeded while the very next call on the toggle (worker thread)
    enumerated nothing. render_endpoints() swallows the error and returns [],
    so it surfaced as "no hardware audio sink found" on a laptop sitting
    right next to its own working speakers.

    Called from every CoreAudio entry point rather than from the threads
    themselves: the threads belong to the UI and the engine, and neither
    should have to know that this backend speaks COM.
    """
    if getattr(_com_state, "ready", False):
        return
    try:
        import comtypes
        comtypes.CoInitialize()
    except Exception:
        pass        # already initialised on this thread, or no comtypes
    _com_state.ready = True


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
        # The IPolicyConfig cache lives in _com_state, keyed by thread --
        # see _policy_config(). Nothing instance-scoped survives here.
        pass

    def render_endpoints(self) -> list:
        """Active render endpoints, as (endpoint_id, friendly_name)."""
        _ensure_com()
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
        _ensure_com()
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
        # Cached per thread, not on self. An interface pointer belongs to the
        # apartment that created it, so handing the UI thread's IPolicyConfig
        # to the engine's worker thread is not something COM promises to
        # honour -- and this object is shared by both.
        _ensure_com()
        if getattr(_com_state, "policy_tried", False):
            return getattr(_com_state, "policy", None)
        _com_state.policy_tried = True
        _com_state.policy = None
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
                _com_state.policy = CoCreateInstance(GUID(clsid),
                                                     interface=iface)
                return _com_state.policy
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

    # -- endpoint volume ---------------------------------------------------
    def _endpoint_volume(self, endpoint_id: str):
        _ensure_com()
        try:
            from comtypes import CLSCTX_ALL, POINTER, cast
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            enumerator = AudioUtilities.GetDeviceEnumerator()
            device = enumerator.GetDevice(endpoint_id)
            iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            return cast(iface, POINTER(IAudioEndpointVolume))
        except Exception:
            return None

    def get_volume(self, endpoint_id: str) -> "tuple | None":
        """(level 0.0-1.0, muted) for one endpoint, or None.

        Scalar rather than dB: it is the quantity the volume keys and the
        system slider move, so mirroring it is a straight copy with no
        curve to get wrong.
        """
        vol = self._endpoint_volume(endpoint_id)
        if vol is None:
            return None
        try:
            return (float(vol.GetMasterVolumeLevelScalar()),
                    bool(vol.GetMute()))
        except Exception:
            return None

    def set_volume(self, endpoint_id: str, level: float, muted: bool) -> bool:
        vol = self._endpoint_volume(endpoint_id)
        if vol is None:
            return False
        try:
            vol.SetMasterVolumeLevelScalar(max(0.0, min(1.0, level)), None)
            vol.SetMute(bool(muted), None)
            return True
        except Exception:
            return False


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
        self._last_trap_volume: "tuple | None" = None
        # Why the last enable() returned None. Read by ui/app.py, which
        # otherwise has only "it failed" to show the user.
        self.last_enable_error: str | None = None

    @property
    def settling(self) -> bool:
        """A candidate output is waiting out its debounce; see
        RoutingBackend.settling."""
        return self._pending is not None

    def relabel(self, real: SinkInfo) -> bool:
        """The trap is the virtual cable's own endpoint, whose name is the
        driver's and not ours to change, so there is nothing to rebuild."""
        return False

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
        output available -- caller should wait and retry).

        On None, last_enable_error says which of the three ways this failed.
        They are not the same problem and the caller cannot tell them apart
        from the return value: the first on-hardware run of this backend
        reported "no hardware audio sink found" when the truth was that COM
        was not initialised on the calling thread, so nothing was enumerated
        at all (see _ensure_com).
        """
        self.last_enable_error = None
        self.recover_stale(self._core)
        endpoints = self._core.render_endpoints()
        if not endpoints:
            self.last_enable_error = (
                "Windows reported no audio output devices at all -- not even "
                "the virtual cable that was found a moment ago. This is a "
                "fault in the app, not in your sound setup.")
            return None
        trap_id = self._find_trap(endpoints)
        if trap_id is None:
            self.last_enable_error = (
                "The virtual audio cable is no longer installed. Reinstall "
                "VB-CABLE and reboot.")
            return None
        trap = self._sink_for_in(endpoints, trap_id)

        previous = self._core.default_render_endpoint()
        if previous == trap_id:
            previous = None         # a stale trap default is not worth restoring
        real = self._pick_real_in(
            endpoints, self.preferred_name,
            self._sink_for_in(endpoints, previous))
        if real is None:
            self.last_enable_error = (
                "The virtual cable is installed, but Windows lists no other "
                "output device to play the filtered audio to. Enable your "
                "speakers or headphones in Sound settings and try again.")
            return None

        # Persisted *before* the swap, so a crash between the two leaves
        # recover_stale() something to work from.
        try:
            ROUTING_STATE.write_text(json.dumps(
                {"previous_default": previous or real.name, "ts": time.time()}))
        except Exception:
            pass        # arming recovery is a convenience, not a precondition
        if not self._core.set_default_render_endpoint(trap_id):
            ROUTING_STATE.unlink(missing_ok=True)
            self.last_enable_error = (
                "Windows refused to make the virtual cable the default "
                "playback device. Nothing has been changed.")
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
        """See RoutingBackend.check() for what each return means.

        All four outcomes, and the distinction between two of them is the
        subtle part: 'real_sink_changed' means the user chose a different
        output and callers remember it, 'real_sink_replaced' means their
        hardware changed under them and callers must NOT -- their choice did
        not change, their devices did. Reporting a yanked USB headset as a
        choice would overwrite the preference they actually set.
        """
        if self.trap is None:
            return None
        endpoints = self._core.render_endpoints()

        trap_id = self._find_trap(endpoints)
        if trap_id is None:
            # The cable was uninstalled or disabled under us. Nothing can be
            # re-asserted and there is nothing left to capture from, so the
            # session has to be rebuilt or stopped -- which is exactly what
            # 'trap_lost' tells the caller.
            self.trap = None
            return "trap_lost"
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
                # preferred_name is deliberately left alone here. Whether an
                # event counts as the user's choice is the caller's call --
                # it is what separates 'real_sink_changed' from
                # 'real_sink_replaced' above -- so the backend reports and
                # does not decide.
                self.real = candidate
                return "real_sink_changed"
            return None

        # The trap still holds the default.
        ids = {eid for eid, _ in endpoints}
        # A pick in Windows' output menu is ONE event: the trap is put back
        # straight away, so the candidate is never seen as the default a
        # second time. Waiting to see it twice meant a single pick was never
        # followed (the same defect PipeWireBackend.check() had). The
        # candidate is confirmed by age and by still existing instead; a
        # flapping one is replaced by each newer sighting and never ages.
        pending, self._pending = self._pending, None
        if pending is not None:
            if pending.name not in ids:
                pass                                  # it was a transient
            elif self.real is not None and pending.name == self.real.name:
                pass                                  # nothing to follow
            elif (time.monotonic() - self._pending_since
                  >= self.RETARGET_DEBOUNCE_S):
                self.real = pending
                return "real_sink_changed"
            else:
                self._pending = pending               # not old enough yet
                return None
        # make sure our output endpoint is still there.
        if self.real is not None and self.real.name not in ids:
            # Their device vanished; a fallback is not a choice they made.
            self.real = self._pick_real_in(endpoints, self.preferred_name, None)
            return "real_sink_replaced" if self.real else "real_sink_lost"
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

    def retarget_playback(self, pid: int, sink_name: str) -> bool:
        """Not possible on WASAPI: a stream is bound to its endpoint when it
        is opened, and neither PortAudio nor sounddevice exposes any way to
        move a running one. base.py's documented answer for that is False,
        which means "use the stop/start path", not "the audio is broken".

        So the live-move optimisation PipeWire gets from C1 does not apply
        here, and a Windows output change costs the full engine restart. It
        is a missing optimisation, not a defect -- and it is why base.py
        keeps pin_stream and this as separate questions rather than assuming
        a platform answers both the same way.
        """
        return False

    # -- volume ------------------------------------------------------------
    def adopt_volume(self) -> None:
        """Seed the mirror from the real endpoint, so inserting the filter
        neither changes how loud anything is nor makes the slider jump."""
        if self.trap is None or self.real is None:
            return
        level = self._core.get_volume(self.real.name)
        if level is None:
            return
        self._core.set_volume(self.trap.name, level[0], level[1])
        self._last_trap_volume = level

    def sync_volume(self) -> "tuple | None":
        """Mirror the cable's volume onto the real endpoint (ROADMAP C2).

        Needed for the same reason as on Linux: while filtering, the cable
        is the system default, so the volume keys and the system slider move
        *it* -- and its render side goes nowhere, because the engine plays to
        the real endpoint directly. Without this the keys are inert.

        Mirrors only on a change, so adjusting the real device directly in a
        mixer is not immediately overwritten, and treats the first reading
        as a baseline rather than a change.

        UNVERIFIED ON HARDWARE, and the one thing to check first: on Linux
        it is measured that a sink's volume does not scale its monitor
        (scripts/spike_c2_monitor_volume.py), so the trap's level is
        harmless to the model. Whether a virtual cable's render volume
        scales what its capture side produces is a property of that driver
        and has not been measured. If it does, the mirror double-attenuates:
        the capture arrives quiet AND the real endpoint is set quiet, so the
        keys act roughly twice as hard. The fix in that case is to pin the
        cable at unity here and mirror only to the real endpoint -- at the
        cost, as on Linux, of the system slider snapping back to 100%.
        """
        if self.trap is None or self.real is None:
            return None
        trap = self._core.get_volume(self.trap.name)
        if trap is None or trap == self._last_trap_volume:
            return None
        first = self._last_trap_volume is None
        self._last_trap_volume = trap
        if first:
            return None      # a baseline reading, not a change to mirror
        if not self._core.set_volume(self.real.name, trap[0], trap[1]):
            return None
        return trap

    def diagnose_capture(self, pid: int) -> "str | None":
        """Is what we are capturing still what we meant to capture?

        Windows offers no graph to inspect -- there is no pw-dump that would
        say which endpoint another process's stream is attached to -- so the
        per-stream question base.py describes cannot be answered here and
        'capture_hijacked' is never reported. base.py permits returning None
        always for exactly this reason.

        Two of its states are still answerable structurally, though, and
        both are worth catching:

          trap_lost     the cable is gone, so the capture endpoint this
                        session was built on no longer exists.
          feedback_loop the device we render to IS the cable we capture
                        from. That is output feeding input, unconditionally
                        wrong and audible, and on Windows it is reachable:
                        a cable's own render endpoint is a perfectly
                        ordinary output that could be picked.
        """
        if self.trap is None:
            return None
        endpoints = self._core.render_endpoints()
        if self._find_trap(endpoints) is None:
            return "trap_lost"
        if self.real is not None and _matches(self.real.description,
                                              TRAP_RENDER_NAMES):
            return "feedback_loop"
        return None


def list_sinks() -> list:
    """Module-level form, for callers that ask before a session exists.
    Mirrors backends/pipewire.list_sinks()."""
    return WindowsBackend()._sinks()
