"""Layer 1 — audio routing session.

RoutingSession is the platform-agnostic name the rest of the app (ui/app.py,
__main__.py) imports; the actual routing mechanism lives behind a
RoutingBackend (backends/base.py). There are two backends: PipeWire on Linux
(backends/pipewire.py) and a WASAPI virtual-cable backend on Windows
(backends/windows.py). They share no mechanism at all -- no sinks, no node
graph, no pw-cli -- only the behaviour base.py specifies, so everything
platform-specific is selected here and nowhere else.

Both backend modules are safe to import on either platform: each one defers
its platform libraries (subprocess tools, pycaw/comtypes) to the call that
needs them, so this module imports cleanly everywhere and the choice is made
at the bottom.

unsupported_reason() is the other half of the switch. A platform with no
backend and a platform whose backend is missing its prerequisites are
different failures, and both have to produce a sentence a user can act on
rather than a traceback from deep inside a subprocess call or a COM call.

SINK_NAME, SINK_DESC and list_sinks() are dispatched by platform too, since
ui/app.py imports them from here to populate and filter its output picker.
The remaining names are PipeWire's own low-level primitives, re-exported
because the Linux dry-run tests and spikes reach for them directly.
"""

import sys

from .backends import pipewire, windows
from .backends.base import RoutingBackend, SinkInfo
from .backends.pipewire import PipeWireBackend
from .backends.windows import WindowsBackend

# PipeWire-only primitives. Not part of the RoutingBackend seam and not
# dispatched: tests/test_routing_dry.py and scripts/spike_c*.py drive the
# PipeWire graph directly, and there is no Windows analogue for any of them
# (no trap node is ever created on Windows -- see backends/windows.py).
from .backends.pipewire import (  # noqa: F401
    create_trap_sink,
    destroy_node,
    find_sinks_named,
    get_default_sink,
    set_default,
)

# (sys.platform prefix, backend class, backend module). Adding a platform
# means adding a row here, not editing any caller.
_BACKEND_BY_PLATFORM = (
    ("linux", PipeWireBackend, pipewire),
    ("win", WindowsBackend, windows),
)


def _backend_for(platform: str):
    """The backend class for a platform, or None if there isn't one."""
    for prefix, backend, _module in _BACKEND_BY_PLATFORM:
        if platform.startswith(prefix):
            return backend
    return None


def _module_for(platform: str):
    """The backend module for a platform, or None if there isn't one.

    Separate from _backend_for() because the module also carries the
    device-enumeration helpers the UI imports, which are not methods on the
    backend class.
    """
    for prefix, _backend, module in _BACKEND_BY_PLATFORM:
        if platform.startswith(prefix):
            return module
    return None


def unsupported_reason() -> str | None:
    """Why audio routing cannot work on this machine, or None when it can.

    Checked once at startup, before anything touches the platform's audio
    graph. Two different failures arrive here and they need different
    wording: a platform with no backend at all, and a platform whose backend
    exists but whose prerequisites are missing. The second is the backend's
    own question, so it is asked rather than guessed at.
    """
    backend = _backend_for(sys.platform)
    if backend is None:
        return (
            "Music Assassin Live does not support this platform yet.\n\n"
            "It routes audio through PipeWire on Linux and a virtual audio "
            "cable on Windows. Both are platform-specific, and there is "
            f"nothing here that would work on {sys.platform}."
        )
    return backend.unsupported_reason()


# Importable on every platform, so callers (ui/app.py, __main__.py) need no
# platform branch of their own. unsupported_reason() above is what stops any
# of this being used somewhere it cannot work; the fallbacks here only keep
# the imports resolving, and are never reached past that guard.
_impl = _module_for(sys.platform) or pipewire

RoutingSession = _backend_for(sys.platform) or PipeWireBackend

#: The trap device's name, which ui/app.py filters out of the output picker.
SINK_NAME = _impl.SINK_NAME
SINK_DESC = _impl.SINK_DESC
#: Real (non-trap) output devices available right now.
list_sinks = _impl.list_sinks
#: Whether a virtual cable's render endpoint exists; None where the question
#: does not apply or cannot be answered. Dispatched here because the Windows
#: installer asks the app this (via --check-cable) rather than testing for it
#: itself -- the two tests disagreed, and the app's is the one that counts.
trap_render_present = _impl.trap_render_present
