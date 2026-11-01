"""Core Audio process-tap routing backend (macOS 14.4+).

The Mac has no trap sink to create and no default output to swap. Instead it
has a native insert-like primitive: a process tap. A private, device-scoped
tap captures what processes send to the current default output, and
`mutedWhenTapped` silences that original audio only while the tap is being
read. The processed result is played through the same output device.

    apps -> process tap (muted while read) -> private aggregate ->
            PortAudio input -> engine/model -> PortAudio output -> speaker

That gives the product behaviour without a virtual driver, an admin install,
or the crash-prone "default device is now a cable" state the Windows and
Linux backends have to recover from. The default output never changes, so the
volume keys keep working and `recover_stale()` is deliberately empty. If the
process dies, the private tap/aggregate die with it and `mutedWhenTapped`
stops muting: the machine fails open.

The low-level ctypes work lives in `CoreAudio`, injected into
`MacOSBackend` exactly as the Windows backend injects its own CoreAudio
wrapper. That keeps the state machine testable with a fake on any platform;
no test needs a Mac, a tap, or the TCC permission.

WHY NO SAMPLE-RATE RESAMPLER HERE. The aggregate inherits the output device's
native rate. PortAudio can request 48 kHz anyway; macOS Core Audio resamples
into that request. Measured on a 44.1 kHz Bluetooth output: the tap format was
44.1 kHz, the PortAudio stream was 48 kHz, and capture was correct. The engine
therefore keeps its existing 48 kHz path on every platform.

Implements the RoutingBackend protocol in backends/base.py; routing.py selects
it on darwin.
"""

import ctypes
import ctypes.util
import os
import platform
import sys
import time
import uuid
from dataclasses import dataclass

from .base import SinkInfo

# The tap is private and never appears in the system output menu. These names
# are for diagnostics and for PortAudio's device list, not user-facing labels.
SINK_NAME = "Music Assassin Tap"
SINK_DESC = "Music Assassin"
AGGREGATE_NAME = "Music Assassin Tap"
TAP_NAME = "Music Assassin"
AGGREGATE_UID_PREFIX = "com.mabengineers.music-assassin.tap"
BUNDLE_ID = "com.mabengineers.music-assassin-live"

# Core Audio and Core Foundation constants. Spelled out because ctypes has no
# header import; the FourCC values are the ABI.
K_AUDIO_OBJECT_SYSTEM = 1
K_CF_STRING_ENCODING_UTF8 = 0x08000100
K_CF_NUMBER_SINT32 = 3
K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL = int.from_bytes(b"glob", "big")
K_AUDIO_OBJECT_PROPERTY_SCOPE_OUTPUT = int.from_bytes(b"outp", "big")
K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT = int.from_bytes(b"dOut", "big")
K_AUDIO_HARDWARE_PROPERTY_DEVICES = int.from_bytes(b"dev#", "big")
K_AUDIO_HARDWARE_PROPERTY_TAP_LIST = int.from_bytes(b"tps#", "big")
K_AUDIO_HARDWARE_PROPERTY_TRANSLATE_PID = int.from_bytes(b"id2p", "big")
K_AUDIO_DEVICE_PROPERTY_DEVICE_UID = int.from_bytes(b"uid ", "big")
K_AUDIO_DEVICE_PROPERTY_NOMINAL_RATE = int.from_bytes(b"nsrt", "big")
K_AUDIO_DEVICE_PROPERTY_STREAM_CONFIG = int.from_bytes(b"slay", "big")
K_AUDIO_OBJECT_PROPERTY_NAME = int.from_bytes(b"lnam", "big")

K_AUDIO_TAP_MUTE_WHEN_TAPPED = 2
K_AUDIO_HARDWARE_NOT_PERMITTED = 1852797029   # 'nope'

# Aggregate dictionary keys are C strings, not FourCCs.
K_AGG_NAME = "name"
K_AGG_UID = "uid"
K_AGG_PRIVATE = "private"
K_AGG_STACKED = "stacked"
K_AGG_TAP_AUTOSTART = "tapautostart"
K_AGG_SUBDEVICES = "subdevices"
K_AGG_MASTER = "master"
K_AGG_TAPS = "taps"
K_SUBDEVICE_UID = "uid"
K_SUBTAP_UID = "uid"
K_SUBTAP_DRIFT = "drift"


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


class _AudioBuffer(ctypes.Structure):
    _fields_ = [
        ("mNumberChannels", ctypes.c_uint32),
        ("mDataByteSize", ctypes.c_uint32),
        ("mData", ctypes.c_void_p),
    ]


class _AudioBufferList(ctypes.Structure):
    _fields_ = [
        ("mNumberBuffers", ctypes.c_uint32),
        ("mBuffers", _AudioBuffer * 1),
    ]


@dataclass
class Device:
    """One output-capable Core Audio device, with the rate a tap inherits."""
    id: int
    uid: str
    name: str
    rate: float

    def as_sink(self) -> SinkInfo:
        return SinkInfo(self.id, self.uid, self.name)


@dataclass
class Tap:
    id: int
    uid: str


@dataclass
class Aggregate:
    id: int
    uid: str
    name: str


def _addr(selector: int,
          scope: int = K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL):
    return _AudioObjectPropertyAddress(selector, scope, 0)


def _status_text(status: int) -> str:
    """A FourCC when the status is one, otherwise the decimal code.

    Core Audio's useful refusals are FourCCs ('nope', 'what', ...); printing
    only the decimal leaves a reader to look it up.
    """
    if 0 <= status < 2 ** 32:
        raw = status.to_bytes(4, "big")
        if all(32 <= b < 127 for b in raw):
            return f"{status} ('{raw.decode('ascii')}')"
    return str(status)


class CoreAudio:
    """Everything this backend needs from Core Audio, and no more.

    Narrow on purpose: each method answers one question or performs one
    action. `MacOSBackend` takes one of these, so the state machine -- which
    is where the interesting failures live -- is testable with a fake on any
    platform. Every fallible method returns None/[]. Only creation returns an
    error string, because the caller has to put it in front of the user.
    """

    def __init__(self):
        self.core = ctypes.CDLL(
            "/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        self.cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        self.objc = ctypes.CDLL(ctypes.util.find_library("objc"))
        self._configure()

    @staticmethod
    def tap_api_available() -> bool:
        """Whether this OS exposes the process-tap API at all."""
        try:
            core = ctypes.CDLL(
                "/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
            return hasattr(core, "AudioHardwareCreateProcessTap")
        except OSError:
            return False

    def _configure(self) -> None:
        core, cf, objc = self.core, self.cf, self.objc

        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]

        core.AudioHardwareCreateProcessTap.restype = ctypes.c_int32
        core.AudioHardwareCreateProcessTap.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        core.AudioHardwareDestroyProcessTap.restype = ctypes.c_int32
        core.AudioHardwareDestroyProcessTap.argtypes = [ctypes.c_uint32]
        core.AudioHardwareCreateAggregateDevice.restype = ctypes.c_int32
        core.AudioHardwareCreateAggregateDevice.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        core.AudioHardwareDestroyAggregateDevice.restype = ctypes.c_int32
        core.AudioHardwareDestroyAggregateDevice.argtypes = [ctypes.c_uint32]

        core.AudioObjectGetPropertyData.restype = ctypes.c_int32
        core.AudioObjectGetPropertyData.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(_AudioObjectPropertyAddress),
            ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p]
        core.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
        core.AudioObjectGetPropertyDataSize.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(_AudioObjectPropertyAddress),
            ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]

        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCStringPtr.restype = ctypes.c_char_p
        cf.CFStringGetCStringPtr.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cf.CFNumberCreate.restype = ctypes.c_void_p
        cf.CFNumberCreate.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFArrayCreate.restype = ctypes.c_void_p
        cf.CFArrayCreate.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
            ctypes.c_void_p]
        cf.CFDictionaryCreate.restype = ctypes.c_void_p
        cf.CFDictionaryCreate.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_long,
            ctypes.c_void_p, ctypes.c_void_p]
        cf.CFRelease.argtypes = [ctypes.c_void_p]

        self._array_callbacks = ctypes.addressof(
            ctypes.c_char.in_dll(cf, "kCFTypeArrayCallBacks"))
        self._dict_key_callbacks = ctypes.addressof(
            ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        self._dict_value_callbacks = ctypes.addressof(
            ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks"))
        self._true = ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value
        self._false = ctypes.c_void_p.in_dll(cf, "kCFBooleanFalse").value

    # -- CF helpers -------------------------------------------------------
    def _cfstr(self, text: str) -> int:
        ptr = self.cf.CFStringCreateWithCString(
            None, text.encode("utf-8"), K_CF_STRING_ENCODING_UTF8)
        if not ptr:
            raise RuntimeError(f"CFStringCreateWithCString failed for {text!r}")
        return ptr

    def _cfnum(self, value: int) -> int:
        raw = ctypes.c_int32(value)
        ptr = self.cf.CFNumberCreate(None, K_CF_NUMBER_SINT32,
                                     ctypes.byref(raw))
        if not ptr:
            raise RuntimeError(f"CFNumberCreate failed for {value!r}")
        return ptr

    def _cfarray(self, values) -> int:
        raw = (ctypes.c_void_p * len(values))(*values)
        ptr = self.cf.CFArrayCreate(
            None, raw, len(values), self._array_callbacks)
        if not ptr:
            raise RuntimeError("CFArrayCreate failed")
        return ptr

    def _cfdict(self, pairs) -> int:
        keys = (ctypes.c_void_p * len(pairs))(
            *[self._cfstr(k) for k, _ in pairs])
        values = (ctypes.c_void_p * len(pairs))(*[v for _, v in pairs])
        ptr = self.cf.CFDictionaryCreate(
            None, keys, values, len(pairs),
            self._dict_key_callbacks, self._dict_value_callbacks)
        for key in keys:
            self.cf.CFRelease(key)
        if not ptr:
            raise RuntimeError("CFDictionaryCreate failed")
        return ptr

    def _cfbool(self, value: bool) -> int:
        return self._true if value else self._false

    def _release(self, *pointers) -> None:
        for ptr in pointers:
            if ptr:
                self.cf.CFRelease(ptr)

    def _cf_to_str(self, ptr) -> str | None:
        if not ptr:
            return None
        direct = self.cf.CFStringGetCStringPtr(ptr, K_CF_STRING_ENCODING_UTF8)
        if direct:
            return direct.decode("utf-8", "replace")
        buf = ctypes.create_string_buffer(2048)
        ok = self.cf.CFStringGetCString(
            ptr, buf, len(buf), K_CF_STRING_ENCODING_UTF8)
        return buf.value.decode("utf-8", "replace") if ok else None

    def _objc_msg(self, obj, selector: str, *args, restype=ctypes.c_void_p,
                  argtypes=()):
        msg = self.objc.objc_msgSend
        msg.restype = restype
        msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, *argtypes]
        return msg(obj, self.objc.sel_registerName(selector.encode()), *args)

    def _objc_class(self, name: str) -> int:
        return self.objc.objc_getClass(name.encode())

    # -- property helpers -------------------------------------------------
    def _get_scalar(self, obj: int, selector: int, ctype,
                    scope: int = K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL):
        addr = _addr(selector, scope)
        size = ctypes.c_uint32(ctypes.sizeof(ctype))
        out = ctype()
        status = self.core.AudioObjectGetPropertyData(
            obj, ctypes.byref(addr), 0, None, ctypes.byref(size),
            ctypes.byref(out))
        return status, out.value

    def _get_cfstring(self, obj: int, selector: int,
                      scope: int = K_AUDIO_OBJECT_PROPERTY_SCOPE_GLOBAL):
        addr = _addr(selector, scope)
        size = ctypes.c_uint32(ctypes.sizeof(ctypes.c_void_p))
        out = ctypes.c_void_p()
        status = self.core.AudioObjectGetPropertyData(
            obj, ctypes.byref(addr), 0, None, ctypes.byref(size),
            ctypes.byref(out))
        text = self._cf_to_str(out) if status == 0 else None
        if out:
            self.cf.CFRelease(out)
        return status, text

    def _get_u32_array(self, obj: int, selector: int):
        addr = _addr(selector)
        size = ctypes.c_uint32(0)
        status = self.core.AudioObjectGetPropertyDataSize(
            obj, ctypes.byref(addr), 0, None, ctypes.byref(size))
        if status != 0 or size.value == 0:
            return status, []
        count = size.value // 4
        raw = (ctypes.c_uint32 * count)()
        status = self.core.AudioObjectGetPropertyData(
            obj, ctypes.byref(addr), 0, None, ctypes.byref(size), raw)
        return status, list(raw)

    def _output_channels(self, device_id: int) -> int:
        addr = _addr(K_AUDIO_DEVICE_PROPERTY_STREAM_CONFIG,
                     K_AUDIO_OBJECT_PROPERTY_SCOPE_OUTPUT)
        size = ctypes.c_uint32(0)
        status = self.core.AudioObjectGetPropertyDataSize(
            device_id, ctypes.byref(addr), 0, None, ctypes.byref(size))
        if status != 0 or size.value < ctypes.sizeof(_AudioBufferList):
            return 0
        raw = ctypes.create_string_buffer(size.value)
        status = self.core.AudioObjectGetPropertyData(
            device_id, ctypes.byref(addr), 0, None, ctypes.byref(size), raw)
        if status != 0:
            return 0
        abl = ctypes.cast(raw, ctypes.POINTER(_AudioBufferList)).contents
        return sum(abl.mBuffers[i].mNumberChannels
                   for i in range(abl.mNumberBuffers))

    # -- device queries ---------------------------------------------------
    def default_output(self) -> Device | None:
        status, device_id = self._get_scalar(
            K_AUDIO_OBJECT_SYSTEM, K_AUDIO_HARDWARE_PROPERTY_DEFAULT_OUTPUT,
            ctypes.c_uint32)
        if status != 0 or not device_id:
            return None
        return self._device_for_id(device_id)

    def _device_for_id(self, device_id: int) -> Device | None:
        _, uid = self._get_cfstring(
            device_id, K_AUDIO_DEVICE_PROPERTY_DEVICE_UID)
        _, name = self._get_cfstring(
            device_id, K_AUDIO_OBJECT_PROPERTY_NAME)
        _, rate = self._get_scalar(
            device_id, K_AUDIO_DEVICE_PROPERTY_NOMINAL_RATE, ctypes.c_double)
        if not uid:
            return None
        return Device(device_id, uid, name or uid, float(rate))

    def output_devices(self) -> list[Device]:
        status, ids = self._get_u32_array(
            K_AUDIO_OBJECT_SYSTEM, K_AUDIO_HARDWARE_PROPERTY_DEVICES)
        if status != 0:
            return []
        devices = []
        for device_id in ids:
            if self._output_channels(device_id) <= 0:
                continue
            device = self._device_for_id(device_id)
            if device is None:
                continue
            # Our private aggregate is visible to this process; it must never
            # be offered as a place to render the processed signal.
            if (device.uid.startswith(AGGREGATE_UID_PREFIX)
                    or device.name == AGGREGATE_NAME):
                continue
            devices.append(device)
        return devices

    def device_exists(self, uid: str) -> bool:
        return any(device.uid == uid for device in self.output_devices())

    def pid_to_process_object(self, pid: int) -> int:
        addr = _addr(K_AUDIO_HARDWARE_PROPERTY_TRANSLATE_PID)
        raw_pid = ctypes.c_int32(pid)
        size = ctypes.c_uint32(4)
        out = ctypes.c_uint32(0)
        status = self.core.AudioObjectGetPropertyData(
            K_AUDIO_OBJECT_SYSTEM, ctypes.byref(addr),
            ctypes.sizeof(raw_pid), ctypes.byref(raw_pid),
            ctypes.byref(size), ctypes.byref(out))
        return out.value if status == 0 else 0

    def tap_ids(self) -> list[int]:
        status, ids = self._get_u32_array(
            K_AUDIO_OBJECT_SYSTEM, K_AUDIO_HARDWARE_PROPERTY_TAP_LIST)
        return ids if status == 0 else []

    def tap_exists(self, tap_id: int) -> bool:
        return tap_id in self.tap_ids()

    def aggregate_exists(self, agg_id: int) -> bool:
        status, _ = self._get_cfstring(agg_id, K_AUDIO_OBJECT_PROPERTY_NAME)
        return status == 0

    # -- tap / aggregate lifecycle ---------------------------------------
    def create_tap(self, output_uid: str, pid: int):
        """Create a private, device-scoped tap excluding `pid`.

        The initializer sets `exclusive = true` ("everything except these
        PIDs"). Do not touch `isExclusive` afterwards: it is a direction flag,
        and flipping it silently inverts the tap to "only these PIDs".
        """
        process_id = self.pid_to_process_object(pid)
        if not process_id:
            return None, "macOS did not translate this process into a Core Audio process object"
        exclusions = None
        device_uid = None
        uuid_text = str(uuid.uuid4())
        uuid_string = None
        ns_uuid = None
        name = None
        desc = None
        try:
            exclusions = self._cfarray([self._cfnum(process_id)])
            device_uid = self._cfstr(output_uid)
            uuid_string = self._cfstr(uuid_text)
            ns_uuid = self._objc_msg(
                self._objc_class("NSUUID"), "alloc")
            ns_uuid = self._objc_msg(
                ns_uuid, "initWithUUIDString:", uuid_string,
                argtypes=(ctypes.c_void_p,))
            tap_class = self._objc_class("CATapDescription")
            desc = self._objc_msg(tap_class, "alloc")
            desc = self._objc_msg(
                desc, "initExcludingProcesses:andDeviceUID:withStream:",
                exclusions, device_uid, ctypes.c_long(0),
                argtypes=(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long))
            if not desc:
                return None, "CATapDescription initializer returned nil"
            name = self._cfstr(TAP_NAME)
            self._objc_msg(desc, "setName:", name,
                           argtypes=(ctypes.c_void_p,), restype=None)
            self._objc_msg(desc, "setPrivate:", ctypes.c_bool(True),
                           argtypes=(ctypes.c_bool,), restype=None)
            self._objc_msg(
                desc, "setMuteBehavior:",
                ctypes.c_long(K_AUDIO_TAP_MUTE_WHEN_TAPPED),
                argtypes=(ctypes.c_long,), restype=None)
            self._objc_msg(desc, "setUUID:", ns_uuid,
                           argtypes=(ctypes.c_void_p,), restype=None)
            tap_id = ctypes.c_uint32(0)
            status = self.core.AudioHardwareCreateProcessTap(
                desc, ctypes.byref(tap_id))
            if status != 0:
                return None, (
                    "AudioHardwareCreateProcessTap failed with OSStatus "
                    f"{_status_text(status)}")
            _, actual_uid = self._get_cfstring(tap_id.value,
                                               int.from_bytes(b"tuid", "big"))
            return Tap(tap_id.value, actual_uid or uuid_text.upper()), None
        finally:
            if desc:
                self._objc_msg(desc, "release", restype=None)
            if ns_uuid:
                self._objc_msg(ns_uuid, "release", restype=None)
            self._release(exclusions, device_uid, uuid_string, name)

    def destroy_tap(self, tap_id: int) -> bool:
        if not tap_id:
            return False
        return self.core.AudioHardwareDestroyProcessTap(tap_id) == 0

    def create_aggregate(self, output_uid: str, tap_uid: str):
        """Create a private aggregate around one output and one tap.

        The output is the main subdevice, so the aggregate has a clock. The
        tap rides as a sub-tap with drift compensation. No nominal rate is
        set: the aggregate inherits the output's rate, and Core Audio
        resamples to whatever PortAudio requests.
        """
        aggregate_uid = f"{AGGREGATE_UID_PREFIX}.{uuid.uuid4()}"
        aggregate_name = f"{AGGREGATE_NAME} {uuid.uuid4().hex[:8]}"
        name = uid = subdevice = subdevices = subtap = taps = None
        master = desc = None
        try:
            name = self._cfstr(aggregate_name)
            uid = self._cfstr(aggregate_uid)
            subdevice = self._cfdict([
                (K_SUBDEVICE_UID, self._cfstr(output_uid)),
            ])
            subdevices = self._cfarray([subdevice])
            subtap = self._cfdict([
                (K_SUBTAP_UID, self._cfstr(tap_uid)),
                (K_SUBTAP_DRIFT, self._cfbool(True)),
            ])
            taps = self._cfarray([subtap])
            master = self._cfstr(output_uid)
            desc = self._cfdict([
                (K_AGG_NAME, name),
                (K_AGG_UID, uid),
                (K_AGG_PRIVATE, self._cfbool(True)),
                (K_AGG_STACKED, self._cfbool(False)),
                (K_AGG_TAP_AUTOSTART, self._cfbool(True)),
                (K_AGG_SUBDEVICES, subdevices),
                (K_AGG_MASTER, master),
                (K_AGG_TAPS, taps),
            ])
            agg_id = ctypes.c_uint32(0)
            status = self.core.AudioHardwareCreateAggregateDevice(
                desc, ctypes.byref(agg_id))
            if status != 0:
                return None, (
                    "AudioHardwareCreateAggregateDevice failed with OSStatus "
                    f"{_status_text(status)}")
            return Aggregate(agg_id.value, aggregate_uid, aggregate_name), None
        finally:
            self._release(desc, name, uid, subdevice, subdevices, subtap,
                          taps, master)

    def destroy_aggregate(self, agg_id: int) -> bool:
        if not agg_id:
            return False
        return self.core.AudioHardwareDestroyAggregateDevice(agg_id) == 0


class MacOSBackend:
    """The RoutingBackend implementation for macOS 14.4+.

    enable() creates a tap and private aggregate around the current default
    output. disable() destroys them. The system default is never changed, so
    there is nothing to restore and no stale state to recover.
    """

    # PortAudio's default 'high' buffering measured ~140 ms of added latency
    # on the built-in speakers; 'low' measured ~63 ms with zero xruns. The
    # CMF Buds (44.1 kHz) stayed around 83 ms either way. This is a property
    # only this backend sets; the engine reads it with getattr.
    stream_latency = "low"

    # A candidate output must hold for this long before we report it. Same
    # reasoning as PipeWireBackend.RETARGET_DEBOUNCE_S, but there is no trap
    # to re-assert: a stale tap would mute the old device while we render to
    # it, so following the new default is the only safe action.
    RETARGET_DEBOUNCE_S = 0.25

    def __init__(self, core: "CoreAudio | None" = None):
        self._core = core if core is not None else CoreAudio()
        self.tap: Tap | None = None
        self.aggregate: Aggregate | None = None
        self.real: SinkInfo | None = None
        self._aggregate_output_uid: str | None = None
        self._monitor_name: str | None = None
        self._pending: SinkInfo | None = None
        self._pending_since: float = 0.0
        # Why the last enable() returned None. Read by ui/app.py, which
        # otherwise has only "it failed" to show the user.
        self.last_enable_error: str | None = None

    # -- availability ------------------------------------------------------
    @staticmethod
    def unsupported_reason() -> "str | None":
        """See RoutingBackend.unsupported_reason()."""
        if not sys.platform.startswith("darwin"):
            return (
                "The macOS backend only runs on macOS, not on "
                f"{sys.platform}.")
        version = platform.mac_ver()[0] or "unknown"
        try:
            parts = tuple(int(p) for p in version.split(".")[:2])
        except ValueError:
            parts = (0, 0)
        if parts < (14, 4):
            return (
                "macOS 14.4 or later is required for Core Audio process "
                f"taps. This Mac reports macOS {version}.\n\n"
                "Update macOS to use Music Assassin Live on this machine.")
        if platform.machine() != "arm64":
            return (
                "The macOS build currently supports Apple Silicon (arm64) "
                f"only. This Mac reports {platform.machine()}.\n\n"
                "The ONNX Runtime macOS wheel used by this app is arm64-only; "
                "an Intel build would need a different runtime.")
        if not CoreAudio.tap_api_available():
            return (
                "This macOS does not expose the Core Audio process-tap API. "
                "macOS 14.4 or later is required.")
        return None

    # -- crash recovery ----------------------------------------------------
    @staticmethod
    def recover_stale() -> None:
        """Nothing to clean up, deliberately.

        The tap and aggregate are private objects owned by this process. If
        the process died, macOS destroyed them; if it is still alive, they
        are not stale. The system default output was never changed, and
        `mutedWhenTapped` stops muting when nobody reads the tap, so a crash
        fails open instead of leaving the machine silent. This is the one
        backend where doing nothing is the correct recovery.
        """
        return

    # -- lifecycle ---------------------------------------------------------
    def enable(self) -> "SinkInfo | None":
        self.last_enable_error = None
        self._destroy_objects()
        self.real = None
        default = self._core.default_output()
        if default is None:
            self.last_enable_error = (
                "macOS reports no output device. Connect speakers or "
                "headphones and try again.")
            return None
        self.real = default.as_sink()
        if not self._activate(default):
            return None
        return self.real

    def _activate(self, output: Device) -> bool:
        """Create the tap/aggregate pair around `output`."""
        tap, error = self._core.create_tap(output.uid, os.getpid())
        if tap is None:
            if any(word in (error or "").lower()
                   for word in ("not permitted", "nope", "permission")):
                self.last_enable_error = self._permission_message()
            else:
                self.last_enable_error = (
                    "macOS could not create the system-audio tap.\n\n"
                    f"{error}")
            return False
        aggregate, error = self._core.create_aggregate(output.uid, tap.uid)
        if aggregate is None:
            self._core.destroy_tap(tap.id)
            self.last_enable_error = (
                "macOS created the tap but could not create its aggregate "
                "device.\n\n"
                f"{error}\n\nThis is a bug in the app, not in your sound "
                "setup.")
            return False
        self.tap = tap
        self.aggregate = aggregate
        self._aggregate_output_uid = output.uid
        self._monitor_name = aggregate.name
        self._pending = None
        self._pending_since = 0.0
        return True

    def _destroy_objects(self) -> None:
        if self.aggregate is not None:
            self._core.destroy_aggregate(self.aggregate.id)
        if self.tap is not None:
            self._core.destroy_tap(self.tap.id)
        self.tap = None
        self.aggregate = None
        self._aggregate_output_uid = None
        self._monitor_name = None

    def disable(self) -> None:
        self._destroy_objects()
        self.real = None
        self._pending = None
        self._pending_since = 0.0

    # -- supervision -------------------------------------------------------
    @property
    def settling(self) -> bool:
        return self._pending is not None

    def check(self) -> "str | None":
        """See RoutingBackend.check() for what each return means."""
        if self.tap is None or self.aggregate is None:
            return None
        if (not self._core.tap_exists(self.tap.id)
                or not self._core.aggregate_exists(self.aggregate.id)):
            return "trap_lost"
        default = self._core.default_output()
        if default is None:
            return "real_sink_lost"
        if self.real is None:
            self.real = default.as_sink()
            return None
        if default.uid != self.real.name:
            # The user picked another output (or the old one vanished and
            # macOS chose a replacement). We never re-assert a default; we
            # follow the new one, but wait out the debounce first so a
            # flapping Bluetooth device does not restart the engine per tick.
            if self._pending is None or default.uid != self._pending.name:
                self._pending = default.as_sink()
                self._pending_since = time.monotonic()
            if (time.monotonic() - self._pending_since
                    >= self.RETARGET_DEBOUNCE_S):
                old_choice = self.real
                self.real = default.as_sink()
                self._pending = None
                if not self._core.device_exists(old_choice.name):
                    return "real_sink_replaced"
                return "real_sink_changed"
            return None
        self._pending = None
        if not self._core.device_exists(self.real.name):
            replacement = self._core.default_output()
            if replacement is None:
                return "real_sink_lost"
            self.real = replacement.as_sink()
            return "real_sink_replaced"
        return None

    def relabel(self, real: SinkInfo) -> bool:
        """The aggregate is private and has no system-visible label."""
        return False

    def sync_volume(self) -> "tuple | None":
        """No-op: the default output is unchanged, so the volume keys work."""
        return None

    def adopt_volume(self) -> None:
        """No-op: there is no trap device to mirror volume onto."""
        return

    def retarget_playback(self, pid: int, sink_name: str) -> bool:
        """Not possible without tearing down the aggregate; use stop/start."""
        return False

    def diagnose_capture(self, pid: int) -> "str | None":
        """The private tap and aggregate are the only capture path we own.

        A feedback loop is structurally impossible: the tap excludes this
        process, and the aggregate is private. The answerable failure is the
        tap/aggregate disappearing (coreaudiod restart, uninstall, a crash
        cleanup that took ours with it).
        """
        if self.tap is None or self.aggregate is None:
            return None
        if (not self._core.tap_exists(self.tap.id)
                or not self._core.aggregate_exists(self.aggregate.id)):
            return "trap_lost"
        return None

    @property
    def monitor_source(self) -> str:
        return self._monitor_name or ""

    # -- engine seam -------------------------------------------------------
    def resolve_stream_devices(self, monitor_source: str,
                               sink_name: str) -> tuple:
        """Find the current aggregate in PortAudio, rebuilding if needed.

        A new private aggregate is invisible to PortAudio until the host API
        is re-initialized. The same refresh is needed after an output change,
        because the aggregate is rebuilt around the new device.
        """
        if self.real is None:
            raise RuntimeError("macOS routing has no output device")
        if self._aggregate_output_uid != self.real.name:
            default = self._core.default_output()
            if default is None:
                raise RuntimeError("macOS routing lost its output device")
            # check() normally updated `real` already, but a second change
            # between that tick and the engine's stop/start must not leave
            # the backend building around a different device from the one it
            # reports.
            self.real = default.as_sink()
            self._destroy_objects()
            if not self._activate(default):
                raise RuntimeError(self.last_enable_error or
                                   "could not rebuild the macOS tap")
        try:
            import sounddevice as sd
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"sounddevice unavailable: {exc}") from exc
        try:
            sd._terminate()
            sd._initialize()
        except Exception:  # noqa: BLE001 — a refresh that fails is not fatal;
            pass           # the device list below decides what is usable
        devices = list(enumerate(sd.query_devices()))
        name = self._monitor_name or monitor_source

        def find(want_name: str, want_input: bool):
            for idx, dev in devices:
                channels = (dev["max_input_channels"] if want_input
                            else dev["max_output_channels"])
                if channels > 0 and dev["name"] == want_name:
                    return idx
            return None

        capture = find(name, True)
        if capture is None:
            raise RuntimeError(
                "macOS created the aggregate device but PortAudio does not "
                f"see {name!r}. This is a bug in the app, not in your sound "
                "setup.")
        aggregate = devices[capture][1]
        if aggregate["max_output_channels"] >= 2:
            playback = capture
        else:
            playback = find(self.real.description, False)
            if playback is None:
                raise RuntimeError(
                    "macOS routing cannot find the real output device "
                    f"{self.real.description!r} in PortAudio.")
        return capture, playback

    def pin_stream(self, pid: int, monitor_source: str,
                   sink_name: str) -> bool:
        """Nothing to do: resolve_stream_devices() names real devices."""
        return True

    @staticmethod
    def _permission_message() -> str:
        return (
            "macOS blocked system-audio capture.\n\n"
            "Open System Settings → Privacy & Security → System Audio "
            "Recording (or Screen & System Audio Recording) and enable "
            "Music Assassin Live, then try again.\n\n"
            "If it is already enabled, reset the permission and retry:\n"
            f"    tccutil reset AudioCapture {BUNDLE_ID}")


def list_sinks() -> list:
    """Module-level form, for callers that ask before a session exists."""
    return [device.as_sink() for device in CoreAudio().output_devices()]


def trap_render_present() -> "bool | None":
    """None: there is no driver or cable to check on this platform."""
    return None
