"""Layer 4 — control panel: on/off, a single Original/De-Musiced mix fader
(with independent mute for each side), live input and output meters, and
a pipeline picker. There is no output picker: the output is whatever the
system's output is, and follows it when it changes. Every control is live —
changing pipeline, the mix, or mute while the engine is running takes effect
immediately, no need to stop first.

tkinter only (stdlib, no extra weight); the widgets ttk cannot draw are in
widgets.py. Supervision loop runs every second: routing.check() re-asserts
the trap sink and retargets the engine when the output device changes
(Bluetooth headset reconnects etc.).

**Layout** (ROADMAP C11) is two columns at a fixed 680 × 430: what the audio
passes through on the left (devices, processing), what it is doing on the
right (signal, mix), a header carrying the power control, and a status bar
welded to the bottom edge. The proportion follows the tools that solve this
same problem — SoundSource, Easy Effects, NVIDIA Broadcast all sit near
1.6:1 — but the reason to adopt it here is specific: the meter and the
0–300 % fader are the two controls that need pixels, and they get them.

**Colour means one thing at a time.** Red is the brand and the quantity the
app exists to change — meter bars, fader fill, the icon. Green is *running*.
Amber is transitional or degraded. Red *text* is a fault, and only ever
text, so the toggle that is on and the stream that died never wear the same
colour in the same place.
"""

import atexit
import json
import math
import os
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox

from .. import diagnostics
from ..audio.engine import AudioEngine
from ..audio.routing import RoutingSession
from ..paths import models_dir, SETTINGS_FILE
from .. import processors
from .widgets import (Dropdown, HoldButton, HSlider, Meter, PillButton, Switch,
                      aa_disc)

BG = "#0d0d10"
PANEL = "#17171c"
HAIR = "#24242c"         # card border: visible, never a line you look at
FIELD = "#1f1f26"        # dropdown / input fill
FIELD_EDGE = "#2f2f38"
TEXT = "#f2f2f4"
SUBTEXT = "#87878f"
FAINT = "#57575f"        # meter gridlines, scale marks
TRACK = "#2b2b32"
KNOB = "#6f6f79"
RED = "#ef4056"
AMBER = "#e0a458"
ON_COLOR = "#2ecc71"
ON_INK = "#08301b"       # text/knob on top of ON_COLOR
OFF_COLOR = "#26262e"
FAULT = "#ff6b7d"        # fault *text*; never a fill, so it cannot be a state

METER_IN = "#4a4a58"     # captured signal: present, but not the point
METER_BARS = 46

# Unscaled geometry. Everything below goes through App.px() so the panel
# holds its proportions on a HiDPI display instead of becoming a stamp.
WIN_W, WIN_H = 680, 430
HEADER_H, STATUS_H = 56, 30
PAD, GAP, CARD_PAD = 16, 12, 13
LEFT_W = 292
DEVICES_H, SIGNAL_H = 79, 150
METER_W, METER_H = 316, 100
FADER_W = 300

UI_FAMILIES = ("Inter", "SF Pro Text", "Helvetica Neue", "Cantarell",
               "Ubuntu", "Noto Sans", "DejaVu Sans")
MONO_FAMILIES = ("JetBrains Mono", "SF Mono", "Menlo", "Fira Mono",
                 "Ubuntu Mono", "DejaVu Sans Mono")


def _pick_family(preferred: tuple, available: set, fallback: str) -> str:
    """Ask for a font by name instead of asking Tk for "Sans" and taking
    whatever X hands over — which is most of why the window looks finished
    on one machine and homemade on another."""
    for name in preferred:
        if name in available:
            return name
    return fallback


def _icon_path() -> Path:
    # PyInstaller onefile: bundled at the archive root (see scripts/build_deb.sh).
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "icon.png"
    return Path(__file__).resolve().parent / "assets" / "icon.png"


def enable_dpi_awareness() -> None:
    """Tell Windows this process does its own scaling, before any window.

    Without it Windows reports 96 DPI to the process whatever the user's
    display scaling is, so _ui_scale() computes 1.0, lays the window out at
    1x, and Windows then bitmap-stretches the finished result to 125/150/200
    %. Everything is legible but soft -- which is what "low res on Windows"
    was. Nothing here is wrong on Linux; X11/Wayland report the real DPI.

    PROCESS_SYSTEM_DPI_AWARE (1) rather than per-monitor: this window is a
    fixed size and reads its scale once at startup, so it has no way to
    re-lay itself out when dragged to a monitor with different scaling.
    System awareness means Windows stretches it in that one case, instead of
    leaving it crisp at the wrong physical size -- the better of the two.

    Must run before the first Tk window exists: Windows latches the mode on
    the first call of the process and ignores the rest. Idempotent, so the
    several entry points that build a window can each just call it.
    """
    if not sys.platform.startswith("win"):
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:       # noqa: BLE001
        pass                # pre-8.1, or a manifest already set the mode
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:       # noqa: BLE001
        pass                # nothing better available; soft beats crashing


class App:
    def __init__(self):
        saved = self._load_settings()
        self.routing = RoutingSession()
        enable_dpi_awareness()      # before tk.Tk() below, or it does nothing
        self.engine: AudioEngine | None = None
        self.mute_dry = saved.get("mute_dry", False)
        self.mute_wet = saved.get("mute_wet", False)
        self.mix_pct = saved.get("mix_pct", 100.0)
        self.midside_enabled = saved.get("midside_enabled", False)
        # Default off, matching AudioEngine.set_stereo: B3 measured it as
        # free on every mono metric across 104 pairs, but those metrics run
        # on the mono downmix and are blind to whether the side channel it
        # restores contains audible music. Until someone listens, off.
        self.stereo_enabled = saved.get("stereo_enabled", False)
        self.bandlimit_enabled = saved.get("bandlimit_enabled", True)
        # Defaults on, matching AudioEngine.set_keep_effects: the only
        # pipeline that can honour it is not worth its latency without it.
        # Harmless under every other model, which ignore the setting.
        self.keep_effects_enabled = saved.get("keep_effects_enabled", True)
        self.atten_db = saved.get("atten_db", 0.0)
        self.smoothing_ms = saved.get("smoothing_ms", 0.0)
        self._initial_pipeline = saved.get("pipeline", "stemgen")
        self._mac_permission_notice = saved.get(
            "macos_permission_notice", False)

        # WM_CLASS must equal the .desktop file's StartupWMClass, or the shell
        # cannot match this window to its icon and shows a generic one.
        self.root = tk.Tk(className="music-assassin-live")
        self.root.title("Music Assassin Live")
        self.scale = self._ui_scale()
        self._build_fonts()
        self.root.geometry(f"{self.px(WIN_W)}x{self.px(WIN_H)}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        if sys.platform == "darwin":
            # Cmd-Q goes through Tk's macOS Quit command, not through
            # WM_DELETE_WINDOW. Route it through the same teardown so the tap
            # and aggregate are destroyed rather than left to process death.
            try:
                self.root.createcommand("::tk::mac::Quit", self._quit)
            except tk.TclError:
                pass

        icon_path = _icon_path()
        if icon_path.is_file():
            self._icon_img = tk.PhotoImage(file=str(icon_path))  # kept alive on self
            self.root.iconphoto(True, self._icon_img)

        # Order matters: header claims the top edge and the status bar the
        # bottom one, so the body gets what is left and neither can be
        # pushed off by content growing in the middle.
        self._build_header()
        self._build_status()
        self._build_body()
        self._build_details()
        self._fit_window()
        self._prev_xruns = 0
        self._prev_fallbacks = 0
        self._resyncs = 0
        # A message stays put long enough to be read. Every tick used to
        # overwrite the line, so "feedback loop detected — turned off" was
        # gone inside a second — from the one screen a user is told to read
        # when the audio stops (ROADMAP C4).
        self._hold_until = 0.0

        self.enabled = False
        # The capture diagnosis costs a pw-dump, and the states it catches
        # are structural — they do not appear and clear on their own between
        # ticks — so it runs every CAPTURE_CHECK_TICKS seconds rather than
        # every one. Fast enough that nobody sits in a feedback loop for
        # long, cheap enough to leave on permanently.
        self._capture_tick = 0
        # Set while a start/stop is in flight. The button is disabled during
        # it, so this is only a second line of defence against re-entry.
        self._busy = False
        self._result = None
        RoutingSession.recover_stale()

        # Three ways out of here that are not _quit(), and all of them used
        # to leave the cable holding the system default -- which is a silent
        # machine, with no message and nothing obviously wrong in Sound
        # settings. recover_stale() above only helps on the *next* launch.
        diagnostics.attach_to_tk(self.root)
        diagnostics.set_restore_hook(self._emergency_restore)
        atexit.register(self._emergency_restore)

        self.root.after(1000, self._tick)
        self.root.after(90, self._wave_tick)

    # -- settings persistence ----------------------------------------------------
    @staticmethod
    def _load_settings() -> dict:
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        try:
            SETTINGS_FILE.write_text(json.dumps({
                "pipeline": self.model.get(),
                "keep_effects_enabled": self.keep_effects_enabled,
                "mix_pct": self.mix_pct,
                "mute_dry": self.mute_dry,
                "mute_wet": self.mute_wet,
                "midside_enabled": self.midside_enabled,
                "stereo_enabled": self.stereo_enabled,
                "bandlimit_enabled": self.bandlimit_enabled,
                "atten_db": self.atten_db,
                "smoothing_ms": self.smoothing_ms,
                "macos_permission_notice": self._mac_permission_notice,
            }))
        except OSError:
            pass  # best-effort; a failed save shouldn't block quitting

    # -- display scale and fonts ------------------------------------------------
    def _ui_scale(self) -> float:
        """One number the whole layout multiplies by.

        The window is a fixed size by design, so it cannot rely on the
        toolkit's own scaling: `tk scaling` grows point-sized fonts inside a
        window whose pixel geometry stays put, which overflows it. Every
        dimension here is in nominal pixels and goes through px() instead,
        and fonts are given in pixels (negative sizes) so they follow the
        same factor rather than a second, independent one.
        """
        override = os.environ.get("MUSIC_ASSASSIN_UI_SCALE")
        if override:
            try:
                return max(0.75, min(3.0, float(override)))
            except ValueError:
                pass
        try:
            dpi = float(self.root.winfo_fpixels("1i"))
        except tk.TclError:
            return 1.0
        # Quarter steps: enough to track the common 125/150/200 % settings,
        # coarse enough that a slightly odd DPI does not produce a layout
        # nobody ever looked at.
        return max(1.0, min(2.5, round(dpi / 96.0 * 4) / 4))

    def px(self, n: float) -> int:
        return int(round(n * self.scale))

    def _fit_window(self) -> None:
        """WIN_H is a floor, not a promise.

        The layout is drawn to a fixed 680 × 430 and the numbers here are
        chosen for it, but the height every row actually takes depends on
        the font that was found, on the display scale, and on the length of
        a translated label. Asking Tk what it needs and growing to that is
        the difference between a panel that is a little taller than drawn
        and one that silently drops its last row off the bottom — which is
        exactly what happened to the suppression-limit row the first time.
        """
        self.root.update_idletasks()
        body_h = max(self._column_height(self.left),
                     self._column_height(self.right))
        need = (self.px(HEADER_H) + self.px(STATUS_H) + 2
                + body_h + 2 * self.px(13))
        self.root.geometry(f"{self.px(WIN_W)}x{max(self.px(WIN_H), need)}")

    def _column_height(self, column) -> int:
        """What a column's cards need, including the gap between them."""
        cards = column.winfo_children()
        return (sum(c.winfo_reqheight() for c in cards)
                + self.px(11) * max(0, len(cards) - 1))

    def _build_fonts(self) -> None:
        available = set(tkfont.families(self.root))
        ui = _pick_family(UI_FAMILIES, available,
                          tkfont.nametofont("TkDefaultFont").actual("family"))
        mono = _pick_family(MONO_FAMILIES, available,
                            tkfont.nametofont("TkFixedFont").actual("family"))
        def f(family, size, **kw):
            return tkfont.Font(family=family, size=-self.px(size), **kw)
        self.f_title = f(ui, 16, weight="bold")
        self.f_sub = f(ui, 10)
        self.f_body = f(ui, 12)
        self.f_bold = f(ui, 12, weight="bold")
        self.f_glyph = f(ui, 13)
        self.f_eyebrow = f(mono, 9)
        self.f_mono = f(mono, 11)
        self.f_tiny = f(mono, 8)

    # -- small layout helpers ----------------------------------------------------
    def _card(self, parent, height: int | None = None) -> tk.Frame:
        """A group. Square corners and a hairline border — see widgets.py for
        why these are not Canvas-drawn."""
        card = tk.Frame(parent, bg=PANEL, highlightthickness=1,
                        highlightbackground=HAIR, highlightcolor=HAIR)
        if height is not None:
            card.configure(height=self.px(height))
            card.pack_propagate(False)
        return card

    def _card_body(self, card, top=10, bottom=12) -> tk.Frame:
        inner = tk.Frame(card, bg=PANEL)
        inner.pack(fill=tk.BOTH, expand=True, padx=self.px(CARD_PAD),
                   pady=(self.px(top), self.px(bottom)))
        return inner

    def _eyebrow(self, parent, text: str) -> tk.Label:
        return tk.Label(parent, text=text.upper(), font=self.f_eyebrow,
                        bg=PANEL, fg=SUBTEXT)

    def _rule(self, parent) -> None:
        tk.Frame(parent, bg=HAIR, height=1).pack(fill=tk.X)

    # -- layout builders ---------------------------------------------------------
    def _build_header(self):
        head = tk.Frame(self.root, bg=BG, height=self.px(HEADER_H))
        head.pack(side=tk.TOP, fill=tk.X)
        head.pack_propagate(False)
        tk.Frame(self.root, bg=HAIR, height=1).pack(side=tk.TOP, fill=tk.X)

        left = tk.Frame(head, bg=BG)
        left.pack(side=tk.LEFT, padx=(self.px(PAD), 0))
        self._badge_img = self._badge_photo()
        if self._badge_img is not None:
            tk.Label(left, image=self._badge_img, bg=BG).pack(
                side=tk.LEFT, padx=(0, self.px(11)))
        title = tk.Frame(left, bg=BG)
        title.pack(side=tk.LEFT)
        tk.Label(title, text="Music Assassin", font=self.f_title,
                 bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(title, text="live music removal", font=self.f_sub,
                 bg=BG, fg=SUBTEXT).pack(anchor="w")

        self.btn = PillButton(head, bg=BG, font=self.f_bold,
                              width=self.px(116), height=self.px(36),
                              command=self._toggle)
        self.btn.pack(side=tk.RIGHT, padx=(0, self.px(PAD)))
        self._paint_button("off")

    def _badge_photo(self):
        """The header icon, at a size that was resampled properly.

        Tk's only scaler is subsample(), which is point sampling: taking
        every fourth pixel of a 128 px icon throws away three quarters of
        the edge information and the result looks smeared rather than small.
        The sizes are pre-rendered instead (assets/icon_NN.png), and the
        nearest one up is chosen for the display scale.
        """
        target = 32 if self.scale < 1.3 else 48 if self.scale < 1.8 else 64
        sized = _icon_path().with_name(f"icon_{target}.png")
        try:
            if sized.is_file():
                return tk.PhotoImage(master=self.root, file=str(sized))
            if getattr(self, "_icon_img", None) is not None:
                step = max(1, min(4, round(128 / target)))
                return self._icon_img.subsample(step, step)
        except tk.TclError:
            pass
        return None

    def _pick_default_model(self, names: list[str]) -> str:
        # macOS's primary case is speech over background music, where the
        # speech enhancer is the stronger live choice. Other platforms keep
        # stemgen, which preserves singing and the stereo image.
        default = "dpdfnet_hr" if sys.platform == "darwin" else "stemgen"
        for candidate in (self._initial_pipeline, default, "stemgen",
                          "dpdfnet_hr", "gtcrn"):
            if candidate and candidate in names:
                return candidate
        return names[0]

    def _build_body(self):
        body = self.body = tk.Frame(self.root, bg=BG)
        body.pack(side=tk.TOP, fill=tk.BOTH, expand=True,
                  padx=self.px(PAD), pady=self.px(13))
        left = self.left = tk.Frame(body, bg=BG, width=self.px(LEFT_W))
        left.pack(side=tk.LEFT, fill=tk.Y)
        # Width is fixed and height is not, so the column cannot be allowed
        # to size itself horizontally. That also means its own requested
        # height is meaningless, which is why _fit_window() adds the cards
        # up rather than asking the column.
        left.pack_propagate(False)
        right = self.right = tk.Frame(body, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                   padx=(self.px(GAP), 0))
        self._build_devices(left)
        self._build_processing(left)
        self._build_signal(right)
        self._build_mix(right)

    def _build_devices(self, parent):
        card = self._card(parent, DEVICES_H)
        card.pack(side=tk.TOP, fill=tk.X)
        inner = self._card_body(card)
        self._eyebrow(inner, "Devices").pack(anchor="w")
        names = processors.available(models_dir())
        self.model = tk.StringVar(value=self._pick_default_model(names))
        self.model_dd = self._picker_row(inner, "Pipeline", self.model, names,
                                         self._on_model_change, top=9)

    def _picker_row(self, parent, label, variable, values, command, top):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill=tk.X, pady=(self.px(top), 0))
        gutter = tk.Frame(row, bg=PANEL, width=self.px(58), height=self.px(28))
        gutter.pack(side=tk.LEFT)
        gutter.pack_propagate(False)
        tk.Label(gutter, text=label, font=self.f_body, bg=PANEL,
                 fg=TEXT).pack(side=tk.LEFT)
        dd = Dropdown(row, variable=variable, values=values, bg=PANEL,
                      field=FIELD, border=FIELD_EDGE, fg=TEXT, muted=SUBTEXT,
                      accent=RED, font=self.f_body, width=self.px(196),
                      height=self.px(28), command=command)
        dd.pack(side=tk.LEFT)
        return dd

    def _build_processing(self, parent):
        card = self._card(parent)
        card.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(self.px(11), 0))
        inner = self._card_body(card, bottom=8)
        self._eyebrow(inner, "Processing").pack(anchor="w")
        self.stereo_sw = self._switch_row(
            inner, "Preserve stereo image",
            "rebuilds the image the model collapses",
            self.stereo_enabled, self._toggle_stereo)
        self.midside_sw = self._switch_row(
            inner, "Mid/side prefilter",
            "emphasises the centre before the model",
            self.midside_enabled, self._toggle_midside)
        self.bandlimit_sw = self._switch_row(
            inner, "Band-limit", "20 Hz – 20 kHz",
            self.bandlimit_enabled, self._toggle_bandlimit)
        # Last, and the only switch here that some pipelines cannot honour:
        # keeping effects needs a model with a separate effects stem, not a
        # setting on a speech enhancer (see processors/mrx.py).
        self.keep_sfx_sw, self.keep_sfx_sub = self._switch_row(
            inner, "Keep sound effects",
            "", self.keep_effects_enabled, self._toggle_keep_effects,
            last=True, want_subtitle=True)
        self._sync_keep_sfx()
        self._build_atten_row(inner)

    def _switch_row(self, parent, title, subtitle, value, command, last=False,
                    want_subtitle=False):
        """-> the Switch, or (Switch, subtitle Label) with want_subtitle.

        The subtitle is handed back only where the caller has to rewrite it
        later; every other row's text is fixed at build time.
        """
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill=tk.X, pady=self.px(5))
        sw = Switch(row, bg=PANEL, track=TRACK, accent=ON_COLOR, knob_off=KNOB,
                    knob_on=ON_INK, value=value, command=command,
                    width=self.px(40), height=self.px(22))
        sw.pack(side=tk.RIGHT)
        text = tk.Frame(row, bg=PANEL)
        text.pack(side=tk.LEFT, anchor="w")
        tk.Label(text, text=title, font=self.f_body, bg=PANEL,
                 fg=TEXT).pack(anchor="w")
        sub = tk.Label(text, text=subtitle, font=self.f_sub, bg=PANEL,
                       fg=SUBTEXT)
        sub.pack(anchor="w")
        if not last:
            self._rule(parent)
        return (sw, sub) if want_subtitle else sw

    def _build_atten_row(self, parent):
        """The two permissiveness controls (ROADMAP B4, C12), for every model.

        Suppression limit was speechdenoiser's alone until the mask shaper
        (audio/maskshape.py) gave every model the same floor; smoothing slows
        how fast a bin's gain may fall, which is what the corpus tags as
        vocal-loss and pumping. Both read 0 = off, today's behaviour.
        """
        self.atten_card = tk.Frame(parent, bg=PANEL)
        self.atten_card.pack(fill=tk.X, pady=(self.px(2), 0))
        tk.Frame(self.atten_card, bg=HAIR, height=1).pack(fill=tk.X)
        self.atten_slider, self.atten_val_lbl = self._slider_row(
            "Suppression limit", self.atten_db, 40, self._atten_label,
            self._on_atten_change, top=7)
        self.smooth_slider, self.smooth_val_lbl = self._slider_row(
            "Smoothing", self.smoothing_ms, 150, self._smoothing_label,
            self._on_smoothing_change, top=5)

    def _slider_row(self, title, value, maxv, label, command, top):
        row = tk.Frame(self.atten_card, bg=PANEL)
        row.pack(fill=tk.X, pady=(self.px(top), 0))
        tk.Label(row, text=title, font=self.f_body, bg=PANEL,
                 fg=TEXT).pack(side=tk.LEFT)
        val = tk.Label(row, text=label(value), font=self.f_mono, bg=PANEL, fg=RED)
        val.pack(side=tk.RIGHT)
        slider = HSlider(row, width=self.px(96), height=self.px(22),
                         color=RED, track=TRACK, bg=PANEL,
                         value=value, minv=0, maxv=maxv,
                         command=command, scale=self.scale)
        slider.pack(side=tk.RIGHT, padx=(0, self.px(10)))
        return slider, val

    def _build_signal(self, parent):
        card = self._card(parent, SIGNAL_H)
        card.pack(side=tk.TOP, fill=tk.X)
        inner = self._card_body(card)
        head = tk.Frame(inner, bg=PANEL)
        head.pack(fill=tk.X)
        self._eyebrow(head, "Signal").pack(side=tk.LEFT)
        self.peak_lbl = tk.Label(head, text="peak —", font=self.f_mono,
                                 bg=PANEL, fg=SUBTEXT)
        self.peak_lbl.pack(side=tk.RIGHT)
        self.meter = Meter(inner, bg=PANEL, bars=METER_BARS, in_color=METER_IN,
                           out_color=RED, grid=FAINT, label_color=SUBTEXT,
                           label_font=self.f_tiny, width=self.px(METER_W),
                           height=self.px(METER_H), gutter=self.px(22))
        self.meter.pack(anchor="w", pady=(self.px(9), 0))
        self.meter.render([], [])

    def _build_mix(self, parent):
        card = self._card(parent)
        card.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(self.px(11), 0))
        inner = self._card_body(card, bottom=10)

        head = tk.Frame(inner, bg=PANEL)
        head.pack(fill=tk.X)
        self._eyebrow(head, "Mix").pack(side=tk.LEFT)
        self.mix_val_lbl = tk.Label(head, text=f"{round(self.mix_pct)} %",
                                    font=self.f_mono, bg=PANEL, fg=RED)
        self.mix_val_lbl.pack(side=tk.RIGHT)

        labels = tk.Frame(inner, bg=PANEL)
        labels.pack(fill=tk.X, pady=(self.px(9), 0))
        self.dry_mute_btn = tk.Button(
            labels, font=self.f_glyph, bg=PANEL, bd=0, relief=tk.FLAT,
            highlightthickness=0, activebackground=PANEL, cursor="hand2",
            padx=0, pady=0, command=self._toggle_mute_dry)
        self.dry_mute_btn.pack(side=tk.LEFT)
        tk.Label(labels, text="Original", font=self.f_body, bg=PANEL,
                 fg=TEXT).pack(side=tk.LEFT, padx=(self.px(6), 0))
        self.wet_mute_btn = tk.Button(
            labels, font=self.f_glyph, bg=PANEL, bd=0, relief=tk.FLAT,
            highlightthickness=0, activebackground=PANEL, cursor="hand2",
            padx=0, pady=0, command=self._toggle_mute_wet)
        self.wet_mute_btn.pack(side=tk.RIGHT)
        tk.Label(labels, text="De-Musiced", font=self.f_body, bg=PANEL,
                 fg=TEXT).pack(side=tk.RIGHT, padx=(0, self.px(6)))
        self._style_mute_btn(self.dry_mute_btn, self.mute_dry)
        self._style_mute_btn(self.wet_mute_btn, self.mute_wet)

        self.mix_slider = HSlider(inner, width=self.px(FADER_W),
                                  height=self.px(24), color=RED, track=TRACK,
                                  bg=PANEL, value=self.mix_pct, minv=0, maxv=300,
                                  markers=(50, 100), marker_color=FAINT,
                                  command=self._on_mix_change, scale=self.scale)
        self.mix_slider.pack(anchor="w", pady=(self.px(7), 0))
        self._build_fader_scale(inner)

        self._rule(inner)
        foot = tk.Frame(inner, bg=PANEL)
        foot.pack(fill=tk.X, pady=(self.px(9), 0))
        HoldButton(foot, bg=PANEL, fg=TEXT, border=FIELD_EDGE, active_bg=FIELD,
                   font=self.f_body, text="⇄  Hold to compare",
                   width=self.px(146), height=self.px(26),
                   command=self._compare).pack(side=tk.LEFT)
        self.boost_lbl = tk.Label(foot, text=self._boost_label(self.mix_pct),
                                  font=self.f_mono, bg=PANEL, fg=SUBTEXT)
        self.boost_lbl.pack(side=tk.RIGHT)

    def _build_fader_scale(self, parent):
        """Marks under the fader, at the positions the handle actually
        reaches — the slider insets by its handle radius at both ends, so
        even spacing across the full width would put 100 % in the wrong
        place, which is the one number on this scale anyone aims at."""
        scale = tk.Frame(parent, bg=PANEL, height=self.px(11),
                         width=self.px(FADER_W))
        scale.pack(anchor="w", pady=(self.px(1), self.px(7)))
        scale.pack_propagate(False)
        pad = self.mix_slider.pad
        usable = self.px(FADER_W) - 2 * pad
        for value, anchor in ((0, "w"), (100, "center"), (300, "e")):
            label = tk.Label(scale, text=f"{value}" + (" %" if value == 300 else ""),
                             font=self.f_tiny, bg=PANEL, fg=FAINT)
            label.place(x=pad + usable * value / 300.0, y=0, anchor="n" + (
                "w" if anchor == "w" else "e" if anchor == "e" else ""))

    def _build_status(self):
        bar = tk.Frame(self.root, bg=BG, height=self.px(STATUS_H))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)
        tk.Frame(self.root, bg=HAIR, height=1).pack(side=tk.BOTTOM, fill=tk.X)

        self.details_on = tk.BooleanVar(value=False)
        self.details_btn = tk.Label(bar, text="details ▸", font=self.f_mono,
                                    bg=BG, fg=SUBTEXT, cursor="hand2")
        self.details_btn.pack(side=tk.RIGHT, padx=(0, self.px(PAD)))
        self.details_btn.bind("<Button-1>", self._toggle_details)
        dot = self.px(9)
        self.status_dot = tk.Canvas(bar, width=dot, height=dot, bg=BG,
                                    highlightthickness=0, bd=0)
        self.status_dot.pack(side=tk.LEFT, padx=(self.px(PAD), self.px(8)))
        self.status = tk.Label(bar, text="idle", font=self.f_mono, bg=BG,
                               fg=SUBTEXT, anchor="w")
        self.status.pack(side=tk.LEFT)
        self._say("idle")

    def _build_details(self):
        """The counters, over the panel rather than pushing it around.

        They are what diagnoses an underrun and they were never an answer to
        "is this working" (ROADMAP C4), so they stay folded away — but a
        fixed-size window has nowhere to grow, and growing it was the reason
        opening this used to move everything on screen.
        """
        self.details = tk.Label(self.root, text="", justify=tk.LEFT, anchor="nw",
                                font=self.f_mono, bg=PANEL, fg=SUBTEXT,
                                highlightthickness=1, highlightbackground=HAIR,
                                padx=self.px(14), pady=self.px(12))

    @staticmethod
    def format_details(rows) -> str:
        """Aligned key/value pairs, one per line.

        The counters used to be four dense lines of `key: value  key: value`
        that had to be read rather than scanned. Reading them happens when
        something is wrong, which is the worst moment to make someone parse
        a line.

        Width 16, not 14: "output backlog" is exactly 14 characters, so at
        the old width it butted straight against its value ("backlog37 ms").
        A column that only holds while no key reaches it is not a column."""
        return "\n".join(f"{k:<16}{v}" for k, v in rows)

    # -- mix / mute callbacks -----------------------------------------------------
    @staticmethod
    def _wet_boost(mix_pct: float) -> float:
        # past 100% the crossfade is already fully wet; extra travel boosts
        # wet gain instead, up to 3x (+9.5 dB) at 300%, to offset
        # post-processing loudness loss. engine._soft_limit keeps this from
        # ever clipping/overdriving the output.
        return 1.0 + max(0.0, mix_pct - 100.0) / 100.0

    @staticmethod
    def _boost_label(mix_pct: float) -> str:
        """The extra wet gain past 100 %, in the unit it is applied in.

        _wet_boost() has always existed and has never been visible: the
        fader's top two thirds change the output level as well as the mix,
        and nothing said by how much."""
        boost = App._wet_boost(mix_pct)
        return f"boost +{20.0 * math.log10(boost):.1f} dB" if boost > 1.001 else "boost +0.0 dB"

    def _on_mix_change(self, value):
        self.mix_pct = value
        self.mix_val_lbl.config(text=f"{round(value)} %")
        self.boost_lbl.config(text=self._boost_label(value))
        if self.engine:
            self.engine.set_intensity(value / 100.0)
            self.engine.set_volumes(wet=self._wet_boost(value))

    @staticmethod
    def _style_mute_btn(btn: tk.Button, muted: bool) -> None:
        btn.config(text="\U0001F507" if muted else "\U0001F50A",
                  fg=RED if muted else SUBTEXT, activeforeground=RED if muted else SUBTEXT)

    def _toggle_mute_dry(self):
        self.mute_dry = not self.mute_dry
        self._style_mute_btn(self.dry_mute_btn, self.mute_dry)
        self._push_mutes()

    def _toggle_mute_wet(self):
        self.mute_wet = not self.mute_wet
        self._style_mute_btn(self.wet_mute_btn, self.mute_wet)
        self._push_mutes()

    def _push_mutes(self):
        if self.engine:
            self.engine.set_volumes(mute_dry=self.mute_dry, mute_wet=self.mute_wet)

    @staticmethod
    def _atten_label(value: float) -> str:
        return "0 dB (unlimited)" if value < 0.5 else f"{round(value)} dB"

    def _on_atten_change(self, value):
        self.atten_db = value
        self.atten_val_lbl.config(text=self._atten_label(value))
        if self.engine:
            self.engine.set_atten_limit(value)

    @staticmethod
    def _smoothing_label(value: float) -> str:
        return "off" if value < 1 else f"{round(value)} ms"

    def _on_smoothing_change(self, value):
        self.smoothing_ms = value
        self.smooth_val_lbl.config(text=self._smoothing_label(value))
        if self.engine:
            self.engine.set_smoothing(value)

    # -- processing switches -----------------------------------------------------
    # The Switch reports the state it moved to, so these no longer flip a
    # variable and then repaint a button from it — the widget is the state,
    # and there is nothing left to get out of step.
    def _toggle_stereo(self, value):
        self.stereo_enabled = bool(value)
        if self.engine:
            self.engine.set_stereo(self.stereo_enabled)

    def _toggle_midside(self, value):
        self.midside_enabled = bool(value)
        if self.engine:
            self.engine.set_midside(self.midside_enabled)

    def _toggle_bandlimit(self, value):
        self.bandlimit_enabled = bool(value)
        if self.engine:
            self.engine.set_bandlimit(self.bandlimit_enabled)

    def _toggle_keep_effects(self, value):
        self.keep_effects_enabled = bool(value)
        if self.engine:
            self.engine.set_keep_effects(self.keep_effects_enabled)

    def _sync_keep_sfx(self):
        """Enable the switch only for a pipeline that has an effects stem,
        and say which way it is. Called on build and on every model change,
        with or without a running engine."""
        name = self.model.get()
        ok = processors.keeps_effects(name)
        self.keep_sfx_sw.set_enabled(ok)
        self.keep_sfx_sub.configure(
            text=("adds the model's effects stem back"
                  if ok else f"needs an effects-stem pipeline; {name} has none"),
            fg=SUBTEXT if ok else FAINT)

    def _compare(self, held: bool):
        """Hold-to-compare: drop to the original while the button is down.

        Deliberately not engine.set_bypass(), which restores intensity 1.0
        on release rather than whatever the fader says — releasing would
        silently move the user's mix to fully wet.
        """
        if self.engine:
            self.engine.set_intensity(0.0 if held else self.mix_pct / 100.0)

    # -- pipeline / output pickers (live-switchable) -----------------------------
    def _on_model_change(self, _evt=None):
        # Before the engine check: the switch must tell the truth about the
        # selected pipeline whether or not a stream is running.
        self._sync_keep_sfx()
        if not (self.enabled and self.engine):
            return
        try:
            proc = processors.create(self.model.get(), models_dir())
        except Exception as e:  # noqa: BLE001 — surface anything to the user
            messagebox.showerror("Pipeline switch failed", str(e))
            return
        self.engine.set_processor(proc)

    def _follow_output(self, sink) -> str:
        """Point the engine at `sink`, cheaply if possible.

        There is no output picker: the system's output is the only choice,
        so this is reached only when the system changed it (or the device
        vanished). Nothing is remembered; the next start follows whatever
        the system has then.
        """
        self.routing.real = sink
        label = sink.description or sink.name
        if not (self.enabled and self.engine):
            return label
        if not self.engine.retarget_output(sink.name):
            self.engine.retarget(self.routing.monitor_source, sink.name)
        # The menu entry names the device it plays to; rebuilding it also
        # drops our capture link, so pin both of our streams again.
        if self.routing.relabel(sink):
            self.routing.pin_stream(os.getpid(), self.routing.monitor_source,
                                    sink.name)
        return label

    # -- actions ---------------------------------------------------------------
    # Button states. Turning on creates the trap sink (polls the graph up to
    # 3 s), loads an ONNX model cold (~0.5 s) and opens a PortAudio stream
    # (another poll up to 3 s). Doing that on the Tk thread froze the UI, so
    # the button could not repaint and a second click did not cancel the wait
    # — it QUEUED, and undid the action the moment the first one finished.
    # Hence: the work moves to a thread, and the button says what it is doing.
    # text, fill, ink, dot, cursor, clickable. The word changes with every
    # state and the dot only agrees with it, so the button never depends on
    # colour alone to say what it is doing.
    _BTN_STATES = {
        "off":      ("OFF",       OFF_COLOR, TEXT,   KNOB,     "hand2", True),
        "starting": ("starting…", OFF_COLOR, AMBER,  AMBER,    "watch", False),
        "on":       ("ON",        ON_COLOR,  ON_INK, ON_INK,   "hand2", True),
        "stopping": ("stopping…", OFF_COLOR, AMBER,  AMBER,    "watch", False),
    }

    def _paint_button(self, state: str) -> None:
        text, fill, ink, dot, cursor, live = self._BTN_STATES[state]
        outline = FIELD_EDGE if state in ("off", "starting", "stopping") else ""
        self.btn.paint(text, fill, ink, dot=dot, enabled=live, cursor=cursor,
                       outline=outline)

    def _toggle(self):
        if self._busy:
            return          # disabled anyway; belt and braces
        if (not self.enabled and sys.platform == "darwin"
                and not self._mac_permission_notice):
            messagebox.showinfo(
                "System audio permission",
                "macOS will ask for System Audio Recording permission.\n\n"
                "Click Allow. If you already denied it, open System Settings "
                "→ Privacy & Security → System Audio Recording (or Screen & "
                "System Audio Recording), enable Music Assassin Live, then "
                "try again.")
            self._mac_permission_notice = True
        if self.enabled:
            self._begin_transition("stopping", self._work_off)
        else:
            self._begin_transition("starting", self._work_on)

    def _begin_transition(self, state: str, work) -> None:
        self._busy = True
        self._result = None
        self._paint_button(state)
        self._say("starting…" if state == "starting" else "stopping…", AMBER)
        # Paint before the work starts, not after: the whole complaint is
        # that nothing visibly happened for several seconds.
        self.root.update_idletasks()
        threading.Thread(target=work, daemon=True).start()
        self.root.after(80, self._poll_transition)

    def _work_on(self) -> None:
        """Runs OFF the Tk thread — must not touch a single widget."""
        try:
            proc = processors.create(self.model.get(), models_dir())
            real = self.routing.enable()
            if real is None:
                self.routing.disable()
                # The backend may know which way it failed; "no sink" is only
                # one of several and was the wrong guess on Windows.
                self._result = ("nosink", None,
                                getattr(self.routing, "last_enable_error", None))
                return
            engine = AudioEngine(proc, self.routing)
            engine.set_intensity(self.mix_pct / 100.0)
            engine.set_volumes(wet=self._wet_boost(self.mix_pct))
            engine.set_midside(self.midside_enabled)
            engine.set_bandlimit(self.bandlimit_enabled)
            engine.set_stereo(self.stereo_enabled)
            engine.set_atten_limit(self.atten_db)
            engine.set_smoothing(self.smoothing_ms)
            engine.set_keep_effects(self.keep_effects_enabled)
            engine.start(self.routing.monitor_source, real.name)
            self.routing.adopt_volume()
            self._result = ("on", engine, real)
        except Exception as e:  # noqa: BLE001 — surfaced on the Tk thread
            try:
                self.routing.disable()
            except Exception:  # noqa: BLE001
                pass
            self._result = ("error", None, e)

    def _work_off(self) -> None:
        """Also off-thread: engine.stop() joins the worker and closes the
        PortAudio stream, which is not instant either."""
        try:
            if self.engine:
                self.engine.stop()
            self.routing.disable()
            self._result = ("off", None, None)
        except Exception as e:  # noqa: BLE001
            self._result = ("error", None, e)

    def _poll_transition(self) -> None:
        if self._result is None:
            self.root.after(80, self._poll_transition)
            return
        kind, payload, extra = self._result
        self._result = None
        self._busy = False
        if kind == "on":
            self.engine = payload
            self.enabled = True
            self._push_mutes()
            self._paint_button("on")
            self._say(f"filtering → {extra.description or extra.name}")
        elif kind == "off":
            self.engine = None
            self.enabled = False
            self._paint_button("off")
            self._say("idle")
        elif kind == "nosink":
            self.enabled = False
            self._paint_button("off")
            self._say("could not start — see the dialog", AMBER)
            messagebox.showwarning(
                "Could not start",
                extra or
                # Linux/PipeWire still has no per-reason text, and there the
                # sleeping-Bluetooth guess is usually right.
                "No hardware audio sink found (Bluetooth asleep?). "
                "Play something / reconnect and try again.")
        else:
            self.engine = None
            self.enabled = False
            self._paint_button("off")
            self._say(f"could not start: {extra}", RED)
            messagebox.showerror("Enable failed", str(extra))

    # How often to verify what we are capturing, in ticks (~1 s each).
    CAPTURE_CHECK_TICKS = 5

    # Blocks that fell back to dry within one tick (~50 blocks) before the
    # line stops saying "healthy". A handful is normal right after a device
    # change; a steady stream means the worker is not keeping up and the
    # user is hearing unprocessed audio without being told.
    FALLBACK_WARN_PER_TICK = 5

    # How long a message survives the once-a-second readout, in seconds. A
    # fault holds far longer because it is the one the user is told to go
    # and read after the audio stops -- and because the tick that would
    # overwrite it fires while they are still reaching for the mouse.
    HOLD_INFO_S = 4.0
    HOLD_FAULT_S = 20.0

    def _say(self, text: str, colour: str = SUBTEXT, hold: bool = True) -> None:
        """Every write to the status line goes through here.

        The line's colour became meaningful with C4's health word, which
        makes a stale colour a lie: a red "stream stopped" left in place
        would tint the next perfectly ordinary message. Setting both every
        time is the only way that stays true as messages are added.

        Messages hold. The supervision tick rewrites this line every second,
        which used to erase "feedback loop detected — turned off" before it
        could be read, and then replace it with "idle" — a state that is
        true and says nothing about why.
        """
        self.status.config(text=text, fg=colour)
        self.status_dot.delete("all")
        self.status_dot.create_image(0, 0, anchor="nw", image=aa_disc(
            self.status_dot, self.px(9), bg=BG, fill=colour))
        if hold:
            self._hold_until = time.monotonic() + (
                self.HOLD_FAULT_S if colour in (RED, FAULT) else self.HOLD_INFO_S)

    def _holding(self) -> bool:
        return time.monotonic() < self._hold_until

    def _toggle_details(self, _evt=None):
        self.details_on.set(not self.details_on.get())
        if self.details_on.get():
            # Over the right column only: reading counters and watching the
            # meter are different activities, and the devices and switches
            # stay reachable while the numbers are up.
            self.details.place(x=self.px(PAD + LEFT_W + GAP),
                               y=self.px(HEADER_H) + 1 + self.px(13),
                               width=self.px(WIN_W - 2 * PAD - LEFT_W - GAP),
                               height=self.body.winfo_height())
            self.details.lift()
            self.details_btn.config(text="details ▾")
        else:
            self.details.place_forget()
            self.details_btn.config(text="details ▸")

    def _lag_text(self) -> str:
        """What the lag measurement has to say, including when it has
        nothing. `unmeasured` is not an error and not zero — it means no
        correction was applied and the stereo rebuild stayed bypassed, which
        is worth seeing, because assuming zero instead is what made the
        rebuild sound doubled (ROADMAP B6)."""
        if self.engine is None:
            return "—"
        state = self.engine.lag_state
        if state == "measured":
            return f"{self.engine.processor_lag_ms:.1f} ms"
        if state == "unmeasured":
            return "unmeasured (rebuild bypassed)"
        if state == "not needed":
            return "not needed"
        return "measuring…"

    def _backlog_text(self) -> str:
        """Depth of the wet FIFO, flagged when it saturates.

        `latency_ms` cannot see this queue (see AudioEngine), so a pipeline
        drifting later shows up here and nowhere else. At the 160 ms cap the
        engine is dropping the oldest audio to keep up, so the word matters
        more than the number.
        """
        if self.engine is None:
            return "—"
        ms = self.engine.output_backlog_ms
        return f"{ms:.0f} ms" + (" — SATURATED" if ms >= 155.0 else "")

    def _device_buffer_text(self) -> str:
        """PortAudio's own in/out buffering, as negotiated.

        Excluded from `added latency`, which measures the pipeline this
        project controls — and therefore invisible until reported here,
        despite being most of the delay a listener hears.
        """
        if self.engine is None:
            return "—"
        lat = self.engine.device_latency_ms
        if lat is None:
            return "—"
        return f"{lat[0]:.1f} + {lat[1]:.1f} ms"

    @staticmethod
    def _level_db(levels: list) -> str:
        """Most recent RMS as dBFS. Silence reads as a floor, not as
        -inf — the distinction being made here is signal vs none."""
        if not levels:
            return "—"
        rms = levels[-1]
        if rms < 1e-5:
            return "silent"
        return f"{20.0 * math.log10(rms):.1f} dB"

    @staticmethod
    def format_status(health: str, device: str, latency_ms: float, lag: str,
                      room: int = 26) -> str:
        """The one line the status bar carries while filtering.

        Health first because it is the answer to the only question the line
        is asked most of the time; the device truncated because names like
        "Family 17h/19h HD Audio Controller Analog Stereo" would otherwise
        push the numbers off the end of the bar.
        """
        if len(device) > room:
            device = device[:room - 1] + "…"
        return f"{health} · {device} · {latency_ms:.0f} ms · lag {lag}"

    def _health(self, stats) -> tuple:
        """(word, colour) for the status line — what a user needs to know
        about whether this is working, in one word."""
        if self.engine is None or not self.engine.stream_ok:
            return "stream stopped", RED
        d_fallback = stats.fallback_blocks - self._prev_fallbacks
        d_xruns = stats.xruns - self._prev_xruns
        if d_fallback >= self.FALLBACK_WARN_PER_TICK:
            return f"struggling ({d_fallback} blocks dry)", AMBER
        if d_xruns:
            return f"{d_xruns} xrun{'s' if d_xruns > 1 else ''}", AMBER
        return "healthy", ON_COLOR

    def _check_capture(self) -> bool:
        """Verify the audio reaching the model is the audio we intended.

        Returns True if it acted (caller should stop this tick). See
        backends/base.py diagnose_capture() for why liveness is not enough:
        a capture stream can be alive, healthy, and wired to the wrong
        thing, and stream_ok reports it as fine because it is fine — it is
        just fine about the wrong signal.
        """
        try:
            state = self.routing.diagnose_capture(os.getpid())
        except Exception:  # noqa: BLE001 — a diagnosis that cannot run must
            # never take the app down with it; the audio path is unaffected
            # by our inability to inspect the graph.
            return False

        if state is None:
            return False

        if state == "feedback_loop":
            # No repair attempt here, deliberately. Re-pinning takes up to
            # 3 s of graph polling, and every one of those seconds is spent
            # howling: our output is feeding our input and compounding. The
            # loop is also evidence the targeting is already broken, so the
            # repair would likely fail anyway. Stop first, explain, let the
            # user switch back on.
            self._turn_off()
            self._say("feedback loop detected (capturing our own output) — turned off",
                      RED)
            return True

        if state == "trap_lost":
            self._turn_off()
            self._say("audio device disappeared — turned off, switch back on to rebuild",
                      RED)
            return True

        # capture_hijacked: wrong source, but not a loop, so nothing is
        # getting worse while we try to fix it in place.
        repaired = False
        try:
            repaired = self.routing.pin_stream(
                os.getpid(), self.routing.monitor_source,
                self.routing.real.name if self.routing.real else "")
        except Exception:  # noqa: BLE001 — fall through to turning off
            repaired = False
        if repaired:
            self._say("capture was mis-routed — reconnected", AMBER)
            return False
        self._turn_off()
        self._say("capture is connected to the wrong device — turned off", RED)
        return True

    def _turn_off(self):
        """Synchronous stop, for failure paths that must not leave a half-on
        state (stream died, feedback loop, trap gone).

        The user-initiated stop goes through _work_off on a thread; this one
        accepts blocking the UI because it is already an emergency and the
        alternative is leaving the trap sink installed.
        """
        if self.engine:
            self.engine.stop()
            self.engine = None
        self.routing.disable()
        self.enabled = False
        self._busy = False
        self._result = None
        self._paint_button("off")

    # -- live level meters -------------------------------------------------------
    def _wave_tick(self):
        """Captured above emitted, on a dB scale (ROADMAP C10).

        The output row alone cannot tell a silent session from an idle one,
        which is the whole difficulty with the "sometimes no audio at all"
        report — every other failure names itself in the status line. With
        both rows the three candidates separate on sight: input moving and
        output flat is the engine or the mix, both flat is capture, and both
        moving with nothing audible is routing past our output.
        """
        ins = self.engine.recent_input_levels() if self.engine else []
        outs = self.engine.recent_levels() if self.engine else []
        self.meter.render(ins, outs)
        self.peak_lbl.config(text=f"peak {self._level_db([max(outs)] if outs else [])}")
        self.root.after(90, self._wave_tick)

    # -- supervision -------------------------------------------------------------
    def _tick(self):
        """Supervision heartbeat. Reschedules in a finally, always.

        Five of _tick_body's exits used to return without rescheduling, so
        the first silent stream death, lost sink, lost trap or detected
        feedback loop ended supervision for the life of the process --
        including across a later switch-on, since the loop is only armed
        once, in __init__. Worst of the five was the _check_capture() exit
        -- the feedback detector itself -- so the first loop both turned
        the app off and disabled the only thing that can catch the next.

        Rescheduling here rather than at each exit makes that structurally
        impossible, and covers an exception out of the body as well.
        """
        try:
            self._tick_body()
        finally:
            # Quicker while a newly chosen output waits out its debounce, so
            # a pick in the system menu is followed in ~0.3 s, not ~2 s.
            self.root.after(300 if self.routing.settling else 1000, self._tick)

    def _tick_body(self):
        # Not while a transition is in flight. _work_on/_work_off run off
        # the Tk thread and cannot clear self.enabled themselves, so during
        # a stop this method would otherwise see enabled=True, an engine
        # whose stream is already closed, and "recover" it -- opening a
        # fresh stream that routing.disable() then destroys the trap sink
        # underneath. That is the 2026-08-18 feedback loop exactly (see
        # backends/pipewire.py diagnose_capture), and _poll_transition
        # drops the engine reference immediately afterwards, so nothing is
        # left that can stop it. _busy is set and cleared on this thread,
        # so the check is a plain read.
        if self._busy:
            return
        if self.enabled and self.engine:
            if not self.engine.stream_ok:
                # PortAudio stream died silently (uncaught callback
                # exception or the device vanishing) — the button would
                # otherwise keep showing "on" with no audio flowing until
                # manually toggled. Try the same recovery as a real
                # sink change; only give up and turn off if that fails too.
                if self.routing.real is None:
                    # Nothing to retarget onto. Recovering into an unknown
                    # sink is how a stream ends up following the default,
                    # which while the trap is installed is a feedback loop.
                    self._turn_off()
                    self._say("audio stream died and no output is available "
                              "— turned off", RED)
                    return
                try:
                    self.engine.retarget(self.routing.monitor_source,
                                         self.routing.real.name)
                    self._say("audio stream recovered automatically", AMBER)
                except Exception as e:  # noqa: BLE001 — see retarget handling below
                    self._turn_off()
                    self._say(f"audio stream died, restart failed: {e}", RED)
                return
            # Volume keys act on the trap while it is the default sink, and
            # its output goes nowhere — so without this they do nothing at
            # all (ROADMAP C2). One cheap wpctl read per tick.
            try:
                self.routing.sync_volume()
            except Exception:  # noqa: BLE001 — a volume mirror that cannot
                # run is a wart, not a reason to interrupt the audio path.
                pass
            event = self.routing.check()
            if event in ("real_sink_changed", "real_sink_replaced") and self.routing.real:
                # Someone chose a device in the system menu, or the one we
                # were using disappeared. Either way follow it now rather
                # than after a debounce — a retarget is a metadata write
                # (C1), so there is nothing left to amortise — and say so,
                # which is the feedback C3 says is missing.
                chosen = event == "real_sink_changed"
                try:
                    label = self._follow_output(self.routing.real)
                except Exception as e:  # noqa: BLE001 — a broken stream must not
                    # wedge the app; drop back to a clean, known-off state
                    # instead of leaving the trap sink stuck as default with
                    # no audio flowing.
                    self._turn_off()
                    self._say(f"retarget failed, turned off: {e}", RED)
                    return
                self._say(f"output → {label}" if chosen
                          else f"previous output disappeared — now on {label}",
                          SUBTEXT if chosen else AMBER)
                return
            elif event == "real_sink_lost":
                self._turn_off()
                self._say("output device lost — turned off", RED)
                return
            elif event == "trap_lost":
                # Our interception device is gone from the system (crash,
                # a PipeWire restart, someone else's cleanup). Nothing is
                # left to re-assert, and audio is already reaching the
                # speakers directly, so off is both the correct state and
                # the current one — say so rather than pretending to filter.
                self._turn_off()
                self._say("audio device disappeared — turned off, switch back on to rebuild",
                          RED)
                return

            # A stall left the capture side behind (engine.CAPTURE_EXCESS_MS):
            # every queue the engine owns is already drained in quiet, but
            # this one is in the device and only a fresh stream clears it.
            # Waits for quiet input, so the restart is not heard.
            if self.engine.needs_resync and self.routing.real:
                behind = self.engine.capture_backlog_ms
                try:
                    self.engine.retarget(self.routing.monitor_source,
                                         self.routing.real.name)
                except Exception as e:  # noqa: BLE001 — see retarget handling above
                    self._turn_off()
                    self._say(f"resync failed, turned off: {e}", RED)
                    return
                self._resyncs += 1
                self._say(f"back in sync: dropped {behind:.0f} ms of delay "
                          "a stall left behind", AMBER)
                return

            self._capture_tick += 1
            if self._capture_tick >= self.CAPTURE_CHECK_TICKS:
                self._capture_tick = 0
                if self._check_capture():
                    return
            s = self.engine.stats if self.engine else None
            if s:
                out = self.routing.real.description or self.routing.real.name \
                    if self.routing.real else "?"
                word, colour = self._health(s)
                self._prev_fallbacks, self._prev_xruns = s.fallback_blocks, s.xruns
                # Latency is measured, not nominal, and it is the first thing
                # anyone watching video wants to know (ROADMAP C7). The
                # measured processor lag sits beside it because it is what
                # decides whether the stereo mask lines up with the audio it
                # shapes, and the symptom when it does not — doubled, smeared
                # audio — says nothing about which number was wrong (B6).
                if not self._holding():
                    self._say(self.format_status(word, out,
                                                 self.engine.latency_ms,
                                                 self._lag_text()),
                              colour, hold=False)
                if self.details_on.get():
                    # The measured processor lag is here because it is the
                    # number that decides whether the stereo rebuild's mask
                    # lines up with the audio it shapes (ROADMAP B6). When
                    # that is wrong the symptom is doubled, smeared audio,
                    # and without a readout there is nothing to report but
                    # the symptom.
                    self.details.config(text=self.format_details((
                        ("model", self.model.get()),
                        ("worker", f"{s.worker_ms_avg:.1f} ms/block  (20 ms budget)"),
                        ("processor lag", self._lag_text()),
                        ("added latency", f"{self.engine.latency_ms:.0f} ms"),
                        ("output backlog", self._backlog_text()),
                        ("device buffer", self._device_buffer_text()),
                        ("stereo", "on" if self.stereo_enabled else "off"),
                        ("mix", f"{round(self.mix_pct)} %"),
                        ("input", self._level_db(self.engine.recent_input_levels())),
                        ("output", self._level_db(self.engine.recent_levels())),
                        ("blocks", s.blocks_in),
                        ("fallbacks", s.fallback_blocks),
                        ("xruns", s.xruns),
                        ("peak backlog", f"{s.peak_backlog / 48:.0f} ms"),
                        ("trimmed", f"{s.backlog_trimmed / 48:.0f} ms"
                                    f"  ({s.skips} skips)"),
                        ("capture delay", f"+{self.engine.capture_backlog_ms:.0f} ms"
                                          f"  ({self._resyncs} resyncs)"))))
        elif not self.enabled and not self._holding():
            self._say("idle", hold=False)
            self.details.config(text="")

    def _emergency_restore(self) -> None:
        """Give the system default back, from a context that may be broken.

        Called from atexit, from the crash hooks and from a Tk callback that
        raised, so it assumes nothing: routing may be mid-operation, the
        engine thread may already be dead, Tk may be unusable. Two rules
        follow -- it must not raise, and it must be safe to call twice,
        since atexit fires after a crash hook has already run.

        disable() first because it knows which device the user was on;
        recover_stale() second because it reads the state file written
        before the swap and so works even if this object is past using.
        Both are no-ops if we never took the default in the first place.
        """
        if not getattr(self, "enabled", False):
            return
        self.enabled = False        # first, so a second call does nothing
        # getattr, not self.engine: the attribute is only created when a
        # stream starts, and this method's whole premise is that it runs in
        # a state nothing else is prepared for.
        engine = getattr(self, "engine", None)
        try:
            if engine is not None:
                engine.stop()
            self.routing.disable()
        except Exception:           # noqa: BLE001
            # recover_stale needs none of this object: it reads the state
            # file written before the swap. Last line of defence.
            try:
                RoutingSession.recover_stale()
            except Exception:       # noqa: BLE001
                pass

    def _quit(self):
        self._save_settings()
        self._turn_off()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    App().run()
