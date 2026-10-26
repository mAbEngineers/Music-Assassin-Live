"""Layer 2 — streaming engine.

One duplex PortAudio stream: capture = trap monitor, playback = real output
device. Device targeting is delegated to a RoutingBackend (backends/base.py)
in two steps — resolve_stream_devices() right before the stream opens,
pin_stream() right after — because "point PortAudio at the right device"
needs two passes on PipeWire (an env var a stream *usually* honors, then an
explicit fix-up for when it doesn't; see backends/pipewire.py for why) and
will need a wholly different mechanism again on Windows. AudioEngine only
knows the two-step shape, not why either platform needs it — that keeps
this module the "portable" layer the architecture doc promises.

The audio callback only moves blocks between ring buffers; inference runs on
a worker thread. If the worker falls behind, the callback emits the dry
signal instead of glitching, and the wet/dry mix is always ramped (20 ms) so
toggling never clicks. Dry is delayed through its own FIFO in lockstep with
the wet one (see _dry_out) so both always refer to the same original
instant — mixing live dry against lagged wet sounded like an echo.

Lockstep is necessary and not sufficient: it pairs the n-th input sample
with the n-th *emitted* sample, and a processor's n-th emitted sample is
made from an earlier input than that (50 ms earlier on dpdfnet_hr, and
nothing like its declared latency — audio/lag.py). Both dry FIFOs are
therefore padded by the measured lag once it is known: _dry_work for the
mask the stereo rebuild derives, _dry_out for the mix the listener hears.
Correcting only the first is what left the dry/wet blend 50 ms apart at
every mix below 100 %.

The wet path is mono end to end (the enhancers are mono speech models), so
the processed signal used to be written to both output channels — measured
in B1 as a total stereo collapse on 104/104 corpus pairs. StereoRebuild
(audio/stereo.py) reconstructs the image from the dry pair instead; the
worker keeps its own dry FIFO for it, drained in the same lockstep as the
callback's, so the pair it re-images is the instant the model actually
processed rather than whatever is live.
"""

import collections
import os
import queue
import threading
import time

import numpy as np

from ..processors.base import StreamProcessor
from .backends.base import RoutingBackend
from .bandlimit import BandlimitFilter
from .lag import LagEstimator
from .maskshape import MaskShaper
from .midside import MidSideFilter
from .stereo import StereoRebuild

SAMPLE_RATE = 48000
BLOCK = 960          # 20 ms
XFADE_BLOCKS = 1     # gain ramp spread over one block == 20 ms

# Headroom every FIFO keeps beyond what the installed processor itself
# buffers. 160 ms used to be the whole cap, which is right for a model that
# answers every block and wrong for one that answers once per chunk: a 1 s
# Spleeter chunk arrived as 1 s of audio, the worker kept the newest 160 ms
# and threw the rest away, and the callback filled the gap with dry -- 16 %
# of the output processed, the other 84 % the original music (measured
# 2026-10-22, tests/test_engine_chunked.py).
BASE_FIFO_BLOCKS = 8

# A stall of the worker (a scheduling hiccup, a model loaded on the UI thread,
# a Bluetooth sink waking up) leaves the callback playing dry for k blocks;
# when the worker catches up, it delivers k blocks at once, and the callback
# takes one out per block in, so those k blocks stay queued: k * 20 ms of
# delay that nothing ever drains (tests/test_engine_chunked.py reproduces
# it). It is trimmed away, once, while the input is quiet --
# a paused video, a gap -- where cutting audio cannot be heard.
QUIET_RMS = 10 ** (-60 / 20)
QUIET_BLOCKS = 3             # 60 ms of quiet before the trim is allowed
BACKLOG_SLACK = BLOCK        # standing backlog tolerated before a trim is worth it
CHUNKED_BLOCKS = 4           # a processor holding more than this answers in chunks

LIMITER_THRESHOLD = 0.8  # below this, output passes through untouched
LIMITER_CEILING = 0.98   # peaks above threshold compress toward this, never past it

_EMPTY_STEREO = np.zeros((0, 2), dtype=np.float32)


def _soft_limit(x: np.ndarray) -> np.ndarray:
    """Memoryless soft-knee limiter: a no-op below LIMITER_THRESHOLD, and a
    smooth tanh compression from there up to LIMITER_CEILING for anything
    louder — so boosting wet gain can't send a hard-clipped (or >1.0)
    signal to the speaker, but normal-level audio is never colored."""
    mag = np.abs(x)
    over = mag > LIMITER_THRESHOLD
    if not np.any(over):
        return x
    span = LIMITER_CEILING - LIMITER_THRESHOLD
    compressed = LIMITER_THRESHOLD + span * np.tanh((mag - LIMITER_THRESHOLD) / span)
    return np.where(over, np.sign(x) * compressed, x)


class EngineStats:
    def __init__(self):
        self.blocks_in = 0
        self.fallback_blocks = 0   # wet wanted but not ready -> dry emitted
        self.overflows = 0
        self.wet_trimmed = 0       # processed samples dropped at the wet FIFO cap
        self.worker_ms_avg = 0.0
        self.xruns = 0
        self.callback_errors = 0   # exception in _callback -> would silently kill the stream
        self.backlog_trimmed = 0   # samples dropped once to correct a startup stall


class AudioEngine:
    def __init__(self, processor: StreamProcessor,
                 backend: RoutingBackend | None = None):
        # Optional because the backend is only ever consulted by start() --
        # the audio path (callback, wet/dry mix, limiter) is pure signal
        # processing that has nothing to say about device targeting. Making
        # it mandatory would mean no engine can be constructed without a
        # working platform routing backend, which needlessly couples the two
        # and makes the processing path untestable on its own.
        self._backend = backend
        self.stats = EngineStats()
        self._in_q: queue.Queue = queue.Queue(maxsize=8)
        self._out = np.zeros((0, 2), dtype=np.float32)  # processed stereo FIFO
        # dry audio delayed through the same FIFO pattern as _out (fed and
        # drained in lockstep, callback-thread-only so no lock needed) —
        # mixing live indata against wet (which lags by the model's
        # algorithmic latency + queue/worker hand-off) produced an audible
        # echo/doubling instead of a blend; see stop()/_callback below.
        self._dry_out = np.zeros((0, 2), dtype=np.float32)
        self._lock = threading.Lock()
        self._running = False
        self._wet_target = 1.0   # mix intensity: 1 = fully processed, 0 = fully original
        self._wet_gain = 0.0     # current interpolated mix (ramped, click-free)
        self._dry_volume = 1.0   # original-signal gain
        self._wet_volume = 1.0   # processed-signal gain
        self._mute_dry = False
        self._mute_wet = False
        self._stream = None
        self._worker = None
        self._levels: collections.deque = collections.deque(maxlen=64)
        # Input RMS beside the output's. Without it a silent session and an
        # idle one produce the same picture, and the one live defect the
        # status line cannot name — a healthy stream with no sound — has no
        # evidence to separate capture from mix from routing.
        self._levels_in: collections.deque = collections.deque(maxlen=64)
        self._lag_ema = 0.0      # samples of pipeline delay, smoothed
        # How far the processor's output trails the dry it is paired with,
        # measured at stream start (audio/lag.py). Needed by the stereo
        # rebuild, which is bypassed until it is known, and by the backlog
        # correction below.
        self._lag_est = LagEstimator(SAMPLE_RATE)
        self._mask_lag: int | None = None
        # Set by the off-thread measurement; the worker picks it up and does
        # the padding itself, so _dry_work is only ever touched by one thread.
        self._pending_lag: int | None = None
        # Same hand-off for the mix path's copy of the dry stream, which is
        # drained by the callback thread and so must be padded there.
        self._mix_pad_pending: int | None = None
        self._mix_pad = 0        # samples of lag correction sitting in _dry_out
        # Set when the estimator ran out of usable windows. The lag is then
        # unknown for the session: no correction is applied anywhere and the
        # UI says so, rather than a wrong correction being applied quietly.
        self._lag_given_up = False
        self._measuring = False
        self._midside = MidSideFilter()
        self._midside_enabled = False
        self._stereo = StereoRebuild()
        self._stereo_enabled = False  # B3 fix; off until measured — see set_stereo()
        # dry stereo held for the rebuild, worker-thread-only. Same lockstep
        # trick as _dry_out: fed every block, drained by exactly what the
        # processor produced, so its head always lines up with the model's
        # output however large the model's internal lag is.
        self._dry_work = np.zeros((0, 2), dtype=np.float32)
        self._bandlimit = BandlimitFilter(SAMPLE_RATE)
        # second instance for the right channel: it is a stateful IIR, so a
        # stereo processor's two channels cannot share one. Unused until a
        # wants_stereo processor exists.
        self._bandlimit_r = BandlimitFilter(SAMPLE_RATE)
        self._bandlimit_enabled = True  # safety hygiene filter — on by default
        self._atten_limit_db = 0.0  # forwarded to the processor if it supports one
        # The permissiveness controls (audio/maskshape.py): suppression floor
        # and smoothing on the chain's own per-bin decision, for every model.
        # Worker-thread-only state; its parameters are plain floats the UI
        # thread may write at any time.
        self._shaper = MaskShaper(SAMPLE_RATE)

        # A model swap asked for from another thread. The worker installs
        # it (see set_processor/_swap_runtime) so that nothing the worker
        # owns is mutated underneath it mid-block.
        self._pending_runtime: StreamProcessor | None = None
        # Set by the worker once a swap is installed, consumed by the
        # callback: _dry_out and _mix_pad belong to the callback thread and
        # so have to be cleared by it.
        self._fifo_reset_pending = False
        # Bumped on every swap, so a lag measurement still in flight when
        # the model changes cannot apply its answer to the new one.
        self._lag_gen = 0

        # The two FIFOs are paired by position, so whenever one side has to
        # drop samples the other must drop the same ones or every later
        # pairing is skewed by the difference (an echo below 100 % mix).
        # Wet dropped by the worker at its cap -> the callback drops that
        # much from the head of _dry_out (handed over under self._lock).
        # Dry dropped by the callback at its cap -> the matching wet has not
        # been produced yet, so the callback skips that much of it later.
        self._quiet_run = 0      # consecutive quiet input blocks, callback-only
        self._dry_drop = 0       # written by worker, consumed by callback
        self._wet_skip = 0       # callback-thread-only

        # (processor, downsampler, upsampler) swapped as one unit so the
        # worker thread never reads a processor paired with the wrong
        # resamplers mid-switch
        self._runtime = self._build_runtime(processor)

        # bound how much processed audio may pile up before we drop old
        # samples (keeps wet path from drifting seconds behind live audio).
        # Sized from the installed processor -- see _apply_caps().
        self._max_out = BLOCK * BASE_FIFO_BLOCKS
        self._apply_caps()

    def _apply_caps(self) -> None:
        """Size the FIFO caps and the input queue for the installed processor.

        A processor's declared `latency_samples` is how much input it may
        hold before answering, and a chunked one answers with all of it at
        once. Every buffer downstream has to be able to hold that much plus
        the old 160 ms of headroom, twice over for the chunk that is being
        emitted while the next one fills. The input queue grows the same
        way, because the worker is blocked for the whole of a chunk's
        inference and blocks keep arriving meanwhile.

        For the block-synchronous models this changes almost nothing (a few
        hundred samples on dpdfnet_hr); it is the chunked ones it exists for.
        """
        proc = self.proc
        held = int(np.ceil(proc.latency_samples * SAMPLE_RATE / proc.sample_rate))
        self._max_out = BLOCK * BASE_FIFO_BLOCKS + 2 * held
        with self._in_q.mutex:
            self._in_q.maxsize = BASE_FIFO_BLOCKS + int(np.ceil(held / BLOCK))

    def _warm_up(self, seconds: float = 0.5) -> None:
        """Run silence through the processor so the first real block meets a
        hot runtime. Errors are swallowed: a processor that dislikes being
        warmed must not stop the stream from opening."""
        proc, down, up = self._runtime
        n = int(SAMPLE_RATE * seconds)
        try:
            if proc.wants_stereo:
                x = np.zeros((n, 2), dtype=np.float32)
            else:
                x = np.zeros(n, dtype=np.float32)
            if down is not None:
                x = down.resample_chunk(x)
            y = proc.feed(x)
            if up is not None and len(y):
                up.resample_chunk(y)
        except Exception:  # noqa: BLE001 — see docstring
            pass
        finally:
            proc.reset()
            if down is not None or up is not None:
                # the resamplers now hold silence; rebuild them clean
                self._runtime = self._build_runtime(proc)

    @staticmethod
    def _build_runtime(processor: StreamProcessor):
        processor.reset()
        if processor.sample_rate != SAMPLE_RATE:
            import soxr
            ch = 2 if processor.wants_stereo else 1
            down = soxr.ResampleStream(
                SAMPLE_RATE, processor.sample_rate, ch, dtype="float32")
            up = soxr.ResampleStream(
                processor.sample_rate, SAMPLE_RATE, ch, dtype="float32")
        else:
            down = up = None
        return processor, down, up

    @property
    def proc(self) -> StreamProcessor:
        return self._runtime[0]

    # -- control -------------------------------------------------------------
    def set_processor(self, processor: StreamProcessor) -> None:
        """Hot-swap the model/pipeline while the stream keeps running.

        Handed to the worker rather than done here. The caller is the UI
        thread, and nearly everything a swap has to reset -- the processor,
        its resamplers, the band-limit IIRs, the mid/side downmix, the
        stereo rebuild, _dry_work -- is read and written by the worker on
        every block. Replacing it from another thread races that, and
        warming the new model here would run its first inference
        concurrently with the worker's next feed() on the same object.

        _swap_runtime has what the swap must actually do, and why each
        piece of it is not optional. With no stream running there is no
        worker to defer to and nothing to race, so it happens inline.
        """
        if not self._running:
            self._runtime = self._build_runtime(processor)
            self._apply_caps()
            self._apply_atten_limit()
            return
        self._pending_runtime = processor

    def set_atten_limit(self, db: float) -> None:
        """How far any bin may be pulled down, in dB; 0 = no limit.

        A processor with its own limit (speechdenoiser's atten_lim_db) gets
        it natively; every other model gets the same control through the
        mask shaper's floor (ROADMAP C12) — never both, which would apply
        the limit twice."""
        self._atten_limit_db = db
        self._apply_atten_limit()

    def _apply_atten_limit(self) -> None:
        setter = getattr(self.proc, "set_atten_limit", None)
        if setter:
            setter(self._atten_limit_db)
            self._shaper.set_params(floor_db=0.0)
        else:
            self._shaper.set_params(floor_db=self._atten_limit_db)

    def set_smoothing(self, release_ms: float) -> None:
        """How fast a bin's gain may fall, in ms; 0 = as fast as the model
        moves it (today's behaviour). Trades "a syllable survives" against
        "music lingers a moment" — see audio/maskshape.py."""
        self._shaper.set_params(release_ms=release_ms)

    @property
    def shaping_active(self) -> bool:
        return not self._shaper.identity

    @property
    def latency_ms(self) -> float:
        """Measured delay between a sample arriving and its processed
        counterpart leaving, in ms.

        Excludes the output device's own buffer, which the engine cannot
        see — so this is the latency the app *adds*, which is the number a
        user watching video wants (ROADMAP C7). Comparable to
        bench_quality.py's `latency_ms` column, which measures the same
        span by cross-correlation.

        The alignment padding in _dry_out is subtracted back out. That pad
        is bookkeeping — it never drains, and it delays which dry sample is
        paired with which wet one rather than delaying the output — so
        counting it would inflate this number by the processor's lag while
        nothing a listener could measure had changed.
        """
        return 1000.0 * max(0.0, self._lag_ema - self._mix_pad) / SAMPLE_RATE

    @property
    def output_backlog_ms(self) -> float:
        """Processed audio sitting in `_out` waiting to be emitted, in ms.

        The blind spot in `latency_ms`. That number reads `_dry_out`, which
        only grows when wet is *starved*; when the worker instead runs ahead
        of the callback, `_out` backs up, every extra sample in it is delay a
        listener hears, and `_dry_out` stays flat because the callback still
        drains a whole block each time. So a pipeline can drift steadily
        later while the reported latency does not move at all.

        Pinned at `_max_out` (160 ms plus twice what the processor holds,
        see _apply_caps) means it is saturated and `_work()` is discarding
        the oldest audio to stay under the cap — which is both the worst case
        for delay and audible as a skip.
        """
        return 1000.0 * len(self._out) / SAMPLE_RATE

    @property
    def device_latency_ms(self) -> tuple[float, float] | None:
        """PortAudio's own (input, output) buffering in ms, as negotiated.
        None before the stream exists.

        `latency_ms` excludes device buffering deliberately: it measures the
        pipeline this project controls. That is the right thing for it to
        measure and the wrong thing to be the only number — at sounddevice's
        default of 'high' the excluded part measured 34.8 + 34.8 ms on the
        reporter's hardware, which is most of what a listener hears, and no
        readout named it.

        Reporting it is all this does. Asking for less (latency='low', 8.7 +
        8.7) was tried on 2026-08-21 and reverted with the shed that shipped
        alongside it: device buffering absorbs the same worker jitter the
        wet FIFO does, so cutting it makes underruns likelier, and underruns
        are audible as processed/unprocessed alternation. If it is revisited
        it should be on its own, with fallback_blocks watched.
        """
        if self._stream is None:
            return None
        try:
            lat = self._stream.latency
        except Exception:  # noqa: BLE001 — same reasoning as stream_ok: a
            return None    # probe that can fail is not a probe
        return 1000.0 * lat[0], 1000.0 * lat[1]

    @property
    def processor_lag_ms(self) -> float | None:
        """How far the processor's output trails its input, in ms, or None
        while it is still being measured or if it never could be."""
        return None if self._mask_lag is None else 1000.0 * self._mask_lag / SAMPLE_RATE

    @property
    def lag_state(self) -> str:
        """`measured`, `measuring`, or `unmeasured` — see audio/lag.py.

        `unmeasured` is a real outcome, not an error: the correction is
        skipped and the stereo rebuild stays bypassed, which is the same
        audio as never having measured. It is surfaced because the
        alternative — silently assuming zero — is what made the rebuild
        sound doubled.
        """
        if self._mask_lag is not None:
            return "measured"
        return "unmeasured" if self._lag_given_up else "measuring"

    def recent_levels(self) -> list:
        """Copy of the most recent output RMS levels, oldest first — for a
        live waveform/level meter."""
        return list(self._levels)

    def recent_input_levels(self) -> list:
        """The same for the captured input, before any processing."""
        return list(self._levels_in)

    @property
    def stream_ok(self) -> bool:
        """False if the underlying PortAudio stream died silently — e.g. an
        uncaught callback exception or the device disappearing mid-stream
        both leave it inactive with no error surfaced, so the caller must
        poll this rather than wait for an exception.

        Never raises, which is the whole point: PortAudio answers
        Pa_IsStreamActive on a closed handle with "Invalid stream pointer"
        rather than False, and a supervisor polling every second turns that
        into an uncaught exception in the UI callback and takes the app down
        with it. A pointer PortAudio will not answer for is a dead stream,
        which is exactly what this is asked to report."""
        if self._stream is None:
            return False
        try:
            return self._stream.active
        except Exception:  # noqa: BLE001 — sd raises PortAudioError, but a
            # health probe that can itself fail is not a health probe.
            return False

    def set_intensity(self, intensity: float) -> None:
        """Music-removal intensity: 0.0 = fully original, 1.0 = fully processed."""
        self._wet_target = float(np.clip(intensity, 0.0, 1.0))

    def set_bypass(self, bypass: bool) -> None:
        self.set_intensity(0.0 if bypass else 1.0)

    def set_midside(self, enabled: bool) -> None:
        """Toggle the mid/side stereo pre-filter (see audio/midside.py):
        suppresses wide-panned content ahead of the enhancer, using stereo
        panning instead of spectral guessing. Off by default — stacks
        with whichever pipeline model is selected."""
        self._midside_enabled = enabled

    def set_stereo(self, enabled: bool) -> None:
        """Toggle stereo reconstruction of the wet path (audio/stereo.py).

        Off by default: it changes what every shipped pipeline sends to the
        speakers, and this project does not flip shipped defaults on an
        untested expectation (see ROADMAP §2.2 for what that cost last time).
        Sweep it first — `bench_quality.py --sweep stereo=off,on` reports the
        suppression it trades for the image — then flip the default on the
        numbers. A no-op for processors that produce stereo themselves.
        """
        self._stereo_enabled = enabled

    def set_bandlimit(self, enabled: bool) -> None:
        """Toggle the ~20 Hz-20 kHz band-limit (see audio/bandlimit.py) on
        the processed output — removes content outside human hearing.
        On by default; adds no algorithmic latency (a cheap IIR filter,
        not the buffered WOLA framing the models use)."""
        self._bandlimit_enabled = enabled

    def set_volumes(self, dry: float | None = None, wet: float | None = None,
                     mute_dry: bool | None = None, mute_wet: bool | None = None) -> None:
        """Independent gain for the original (dry) and de-musiced (wet) paths."""
        if dry is not None:
            self._dry_volume = float(dry)
        if wet is not None:
            self._wet_volume = float(wet)
        if mute_dry is not None:
            self._mute_dry = mute_dry
        if mute_wet is not None:
            self._mute_wet = mute_wet

    def start(self, monitor_source: str, sink_name: str) -> None:
        import sounddevice as sd

        if self._backend is None:
            raise RuntimeError(
                "AudioEngine.start() needs a RoutingBackend; construct it as "
                "AudioEngine(processor, backend). It is optional only for "
                "callers that never stream (offline processing, tests).")

        # first targeting pass — see backend.resolve_stream_devices() for
        # why this is platform-specific and why it isn't sufficient alone.
        capture_dev, playback_dev = self._backend.resolve_stream_devices(
            monitor_source, sink_name)

        # Warm the processor BEFORE the stream exists. The first inference
        # after load is slow — measured at 26 blocks (~0.5 s) for
        # dpdfnet_hr against 1 block once hot — and anything the callback
        # sees during that time piles up in the dry FIFO, whose depth then
        # IS the session's latency. Cold start was baking in 140 ms of
        # permanent delay instead of 20 ms, varying run to run with
        # scheduling. Paying it here costs a moment on the button, which is
        # now a state the button can show.
        self._warm_up()
        self.proc.reset()
        self._midside.reset()
        self._bandlimit.reset()
        self._bandlimit_r.reset()
        self._stereo.reset()
        self._shaper.reset()
        self._dry_work = np.zeros((0, 2), dtype=np.float32)
        self._lag_est = LagEstimator(SAMPLE_RATE)
        self._mask_lag = None
        self._pending_lag = None
        self._mix_pad_pending = None
        self._mix_pad = 0
        self._lag_given_up = False
        self._measuring = False
        self._levels.clear()
        self._levels_in.clear()
        self._running = True
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

        self._stream = sd.Stream(
            device=(capture_dev, playback_dev), samplerate=SAMPLE_RATE,
            blocksize=BLOCK, channels=2, dtype="float32",
            callback=self._callback)
        self._stream.start()

        # second targeting pass, once the stream (and its stream nodes)
        # actually exist — see backend.pin_stream().
        if not self._backend.pin_stream(os.getpid(), monitor_source, sink_name):
            print("warning: could not pin audio streams to their targets; "
                  "routing may be wrong (check pw-link -l)")

    def stop(self) -> None:
        self._running = False
        if self._stream:
            # Drop the reference whatever happens. stop()/close() raise when
            # the device is already gone — the one case a teardown has to
            # survive — and leaving a closed handle behind is what made the
            # next stream_ok poll throw instead of reporting the death.
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001 — see above
                pass
            finally:
                self._stream = None
        if self._worker:
            self._worker.join(timeout=2)
            self._worker = None
        # drop any blocks the worker didn't get to — without this, a
        # restart replays up to maxsize blocks (160 ms) of stale pre-stop
        # audio through the freshly-reset processor before catching up to
        # live audio, an audible glitch right after every retarget.
        with self._in_q.mutex:
            self._in_q.queue.clear()
        with self._lock:
            self._out = np.zeros((0, 2), dtype=np.float32)
            self._dry_drop = 0
        self._dry_out = np.zeros((0, 2), dtype=np.float32)
        self._wet_skip = 0
        self._dry_work = np.zeros((0, 2), dtype=np.float32)
        self._lag_ema = 0.0
        self._mask_lag = None
        self._pending_lag = None
        self._mix_pad_pending = None
        self._mix_pad = 0
        self._lag_given_up = False
        self._measuring = False
        self._pending_runtime = None
        self._fifo_reset_pending = False
        self._lag_gen += 1
        self._wet_gain = 0.0

    def retarget(self, monitor_source: str, sink_name: str) -> None:
        """Output device changed (headset plugged/unplugged).

        The heavy path: full teardown and rebuild. Try retarget_output()
        first — this one costs a multi-second dropout and a reset model.
        """
        self.stop()
        self.start(monitor_source, sink_name)

    def retarget_output(self, sink_name: str) -> bool:
        """Move just the playback endpoint, keeping the stream, the model's
        hidden state and every buffer alive (ROADMAP C1).

        An output change does not concern the capture side or the processor,
        so nothing about them needs to be disturbed. Returns False if the
        live move was not possible, meaning the caller should fall back to
        retarget(); it deliberately does not fall back on its own, because
        the caller is the one that knows whether a heavyweight rebuild is
        acceptable right now.
        """
        if self._backend is None or not self.stream_ok:
            return False
        mover = getattr(self._backend, "retarget_playback", None)
        if mover is None:
            return False           # backend predates C1; heavy path only
        try:
            return bool(mover(os.getpid(), sink_name))
        except Exception:  # noqa: BLE001 — any failure just means "fall back"
            return False

    # -- audio path ------------------------------------------------------------
    def _callback(self, indata, outdata, frames, _time, status) -> None:
        if status:
            self.stats.xruns += 1
        self.stats.blocks_in += 1
        try:
            self._callback_body(indata, outdata, frames)
        except Exception:  # noqa: BLE001 — sounddevice silently kills the
            # whole stream on any exception out of this callback (it goes
            # inactive with no error surfaced to the app — the GUI keeps
            # showing "on" with no audio, needing a manual off/on to
            # recover). Never let that happen: fall back to plain dry
            # passthrough for this block and keep the stream alive.
            self.stats.callback_errors += 1
            outdata[:] = indata

    def _callback_body(self, indata, outdata, frames) -> None:
        # The measurement lands on the worker thread; the FIFO it corrects
        # here belongs to this one. Delaying dry by the processor's lag is
        # what makes the mix a blend rather than a blend with an echo in it
        # — below 100 % the two sides were 50 ms apart on dpdfnet_hr.
        # Costs one dry gap of that length, once, at stream start.
        # A swap completed on the worker thread. Only this thread may touch
        # _dry_out, so its reset lands here: the FIFO holds audio the old
        # model was aligned to plus that model's alignment pad, and carrying
        # either forward is precisely the misalignment the pad exists to
        # prevent.
        if self._fifo_reset_pending:
            self._fifo_reset_pending = False
            self._dry_out = np.zeros((0, 2), dtype=np.float32)
            self._mix_pad = 0
            self._wet_skip = 0
            self._lag_ema = 0.0
            # The one flush. The worker cleared _in_q and _out when it
            # installed the model, but this thread may already have been
            # past its check for the flag, and then put one more block into
            # _in_q (and its dry into _dry_out) after that clear. Wiping
            # _dry_out here without wiping _in_q again would leave that
            # block to be answered, paired with dry that is gone: wet one
            # block (20 ms) ahead of dry for the rest of the session. So
            # everything that holds audio is emptied together, here, by the
            # thread that fills it; the worker waits (_work) until this ran,
            # so nothing is in flight while it does.
            with self._in_q.mutex:
                self._in_q.queue.clear()
            with self._lock:
                self._out = np.zeros((0, 2), dtype=np.float32)
                self._dry_drop = 0
        pad = self._mix_pad_pending
        if pad is not None:
            self._mix_pad_pending = None
            if pad > 0:
                self._dry_out = np.concatenate(
                    [np.zeros((pad, 2), dtype=np.float32), self._dry_out])
                self._mix_pad += pad
        try:
            self._in_q.put_nowait(indata.copy())
            self._dry_out = np.concatenate([self._dry_out, indata])
            # The cap grows by the alignment pad: trimming from the head is
            # exactly what would throw the correction away again.
            cap = self._max_out + self._mix_pad
            if len(self._dry_out) > cap:
                cut = len(self._dry_out) - cap
                self._dry_out = self._dry_out[cut:]
                # The wet those dry samples were waiting for is still to
                # come; skip it on arrival so the pairing stays positional.
                self._wet_skip += cut
        except queue.Full:
            self.stats.overflows += 1
            # dropped this block from processing — skip it here too, so the
            # dry FIFO never gets ahead of what wet will actually produce

        with self._lock:
            drop, self._dry_drop = self._dry_drop, 0
            # Both caps can fire over one stall, and then they name the SAME
            # samples: the oldest wet the worker trimmed is exactly the wet
            # this side was waiting to skip because its dry is already gone.
            # Net them, or each side drops that stretch twice.
            both = min(drop, self._wet_skip)
            drop -= both
            self._wet_skip -= both
            if self._wet_skip and len(self._out):
                skip = min(self._wet_skip, len(self._out))
                self._out = self._out[skip:]
                self._wet_skip -= skip
            take = min(frames, len(self._out))
            wet_st = self._out[:take]
            self._out = self._out[take:]
        if drop:
            # The worker discarded that much of the oldest wet; the dry it
            # was paired with sits at the head of this FIFO. Dropping it here
            # keeps every later pairing the same instant — without this a
            # single saturation left dry and wet apart for the session.
            self._dry_out = self._dry_out[drop:]

        # dry pulled from the same lockstep FIFO as wet (both fed once per
        # callback, both drained by the same amount here) so they always
        # refer to the same original instant, however large the model's
        # actual latency turns out to be — using live indata instead made
        # the dry/wet mix sound like an echo rather than a blend.
        take_dry = min(take, len(self._dry_out))
        dry = np.empty_like(indata)
        dry[:take_dry] = self._dry_out[:take_dry]
        if take_dry < frames:
            dry[take_dry:] = indata[take_dry:]  # startup-only fallback
        self._dry_out = self._dry_out[take_dry:]
        # What is left in the dry FIFO after the drain IS the pipeline's
        # delay: input that has been fed but whose processed counterpart is
        # not out yet. Measured rather than summed from nominal parts —
        # the model's own latency_samples is only one contributor, alongside
        # the resamplers, the queue hand-off and (when on) the stereo
        # rebuild, and the sum of the documented figures has never matched
        # what bench_quality measures end-to-end. Smoothed because _out is
        # filled by another thread, so any single reading jitters by up to a
        # block.
        self._trim_standing_backlog(indata)
        self._lag_ema = 0.9 * self._lag_ema + 0.1 * len(self._dry_out)

        wet = np.empty_like(dry)
        wet[:take] = wet_st
        if take < frames:
            wet[take:] = dry[take:]  # underrun tail: fall back to dry
            if self._wet_gain > 0.01:
                self.stats.fallback_blocks += 1

        # per-block linear ramp toward target — click-free toggle
        g0, g1 = self._wet_gain, self._wet_target
        if g0 != g1:
            step = 1.0 / (XFADE_BLOCKS * frames)
            g1 = g0 + np.clip(g1 - g0, -step * frames, step * frames)
            ramp = np.linspace(g0, g1, frames, dtype=np.float32)[:, None]
            self._wet_gain = float(g1)
        else:
            ramp = g0
        dry_g = 0.0 if self._mute_dry else self._dry_volume
        wet_g = 0.0 if self._mute_wet else self._wet_volume
        mixed = dry * dry_g * (1.0 - ramp) + wet * wet_g * ramp
        outdata[:] = _soft_limit(mixed) if wet_g > 1.0 else mixed
        self._levels.append(float(np.sqrt(np.mean(np.square(outdata)))))
        self._levels_in.append(float(np.sqrt(np.mean(np.square(indata)))))

    def _trim_standing_backlog(self, indata: np.ndarray) -> None:
        """Callback thread. Cut the processed audio still queued after this
        block's own take, and the dry it is paired with, once the input has
        been quiet a moment. The worker refills one block before the next
        callback, so nothing is left to underrun.

        Both FIFOs lose the same count from their heads, so the pairing is
        untouched; only the delay between them and the live input shrinks.
        Skipped for a chunked processor, whose output normally sits in the
        FIFO a chunk at a time, and while either side still owes the other
        a drop.
        """
        if float(np.sqrt(np.mean(np.square(indata)))) < QUIET_RMS:
            self._quiet_run += 1
        else:
            self._quiet_run = 0
            return
        if (self._quiet_run < QUIET_BLOCKS or self._wet_skip
                or self.proc.latency_samples > CHUNKED_BLOCKS * BLOCK):
            return
        with self._lock:
            if self._dry_drop:
                return
            cut = min(len(self._out), len(self._dry_out))
            if cut < BACKLOG_SLACK:
                return
            self._out = self._out[cut:]
            self._dry_out = self._dry_out[cut:]
        self.stats.backlog_trimmed += cut

    def _swap_runtime(self, processor: StreamProcessor) -> None:
        """Install a new model. Worker thread only — see set_processor.

        A live model change is a cold start that happens to occur
        mid-stream, so everything start() resets before a model is first
        allowed to see audio has to be reset here as well:

          * The new processor is warmed before it goes live. The first
            inference after load is slow -- 26 blocks (~0.5 s) for
            dpdfnet_hr against 1 block once hot -- and audio arriving
            during it piles up in the dry FIFO, whose depth then IS the
            session's added latency. Warming costs a moment of dry
            passthrough; not warming baked in ~140 ms for the rest of the
            session, permanently, with nothing to work it back off.

          * The measured lag belongs to the model it was measured on, and
            the spread across shipped models is large (gtcrn ~25 ms,
            dpdfnet_hr ~70 ms). Keeping it means the stereo rebuild shapes
            one model's output with another's mask, and the mix blends dry
            against wet up to ~45 ms apart -- which is the echo the
            padding exists to remove. Re-measure rather than carry it.

          * The old model's queued and processed audio is dropped rather
            than spliced onto the front of the new model's first output.
        """
        self._runtime = self._build_runtime(processor)
        self._apply_caps()
        self._apply_atten_limit()
        self._warm_up()
        # worker-owned DSP state, all of it specific to the model that just
        # left: stateful IIRs, the mid/side downmix, the rebuild's history.
        self._midside.reset()
        self._bandlimit.reset()
        self._bandlimit_r.reset()
        self._stereo.reset()
        self._shaper.reset()
        self._dry_work = np.zeros((0, 2), dtype=np.float32)
        self._lag_gen += 1          # invalidates any measurement in flight
        self._lag_est = LagEstimator(SAMPLE_RATE)
        self._mask_lag = None
        self._pending_lag = None
        self._mix_pad_pending = None
        self._lag_given_up = False
        self._measuring = False
        with self._in_q.mutex:
            self._in_q.queue.clear()
        with self._lock:
            self._out = np.zeros((0, 2), dtype=np.float32)
            self._dry_drop = 0
        # _dry_out and _mix_pad are the callback's; it clears them on its
        # next block. That is also what drops the old model's alignment
        # padding, rather than leaving it to delay the new model's dry side
        # by a correction measured for a different pipeline.
        self._fifo_reset_pending = True

    def _work(self) -> None:
        while self._running:
            pending = self._pending_runtime
            if pending is not None:
                self._pending_runtime = None
                self._swap_runtime(pending)
            if self._fifo_reset_pending:
                # The callback is about to flush every queue at once; a
                # block taken now would be answered after that flush with
                # its dry already gone.
                time.sleep(0.002)
                continue
            try:
                block = self._in_q.get(timeout=0.2)
            except queue.Empty:
                continue
            self._process_block(block)

    def _process_block(self, block: np.ndarray) -> None:
        """One captured block through the wet path and into `_out`.

        The whole of the worker's per-block job, split out of the loop so a
        test can drive the real engine one block at a time
        (tests/test_engine_chunked.py) instead of re-implementing it.
        """
        proc, down, up = self._runtime
        t0 = time.perf_counter()
        y = (self._wet_stereo_proc(proc, down, up, block)
             if proc.wants_stereo
             else self._wet_mono_proc(proc, down, up, block))
        ms = (time.perf_counter() - t0) * 1000.0
        self.stats.worker_ms_avg = 0.9 * self.stats.worker_ms_avg + 0.1 * ms
        if len(y) == 0:
            return
        with self._lock:
            self._out = np.concatenate([self._out, y.astype(np.float32)])
            if len(self._out) > self._max_out:
                cut = len(self._out) - self._max_out
                self._out = self._out[cut:]
                # Tell the callback to drop the dry these were paired with.
                self._dry_drop += cut
                self.stats.wet_trimmed += cut

    def _wet_mono_proc(self, proc, down, up, block) -> np.ndarray:
        """The shipped path: stereo in, mono model, stereo back out.

        Returns (n, 2) either way — with the image rebuilt from the dry pair
        when enabled, or the mono signal duplicated (the pre-B3 behaviour)
        when not, so the callback never has to care which.
        """
        # Kept fed unconditionally rather than only while the rebuild is on:
        # this array is worker-thread-only, and letting the toggle mutate it
        # from the UI thread would race the concatenate below for no gain.
        self._dry_work = np.concatenate([self._dry_work, block])
        if len(self._dry_work) > self._max_out:
            self._dry_work = self._dry_work[-self._max_out:]

        mono = (self._midside.process(block) if self._midside_enabled
                else block.mean(axis=1))
        if len(mono) == 0:
            return _EMPTY_STEREO
        x = down.resample_chunk(mono) if down is not None else mono
        y = proc.feed(x)
        if up is not None and len(y):
            y = up.resample_chunk(y)
        if len(y) and self._bandlimit_enabled:
            y = self._bandlimit.process(y)
        if len(y) == 0:
            return _EMPTY_STEREO

        n = min(len(y), len(self._dry_work))
        dry_st, self._dry_work = self._dry_work[:n], self._dry_work[n:]
        if n == 0:
            return np.repeat(y[:, None], 2, axis=1)

        # FIFO position alone does NOT align these. The processor's output
        # trails the input it is paired with — by 50 ms for dpdfnet_hr, and
        # by nothing like its nominal latency_samples (audio/lag.py). Shaping
        # the dry pair with a mask measured 50 ms away from it is what made
        # the stereo rebuild sound doubled and out of sync when it shipped.
        if self._mask_lag is None:
            self._collect_lag(dry_st, y[:n])
            # Until it is known the rebuild stays bypassed rather than
            # running misaligned — a wrong image is worse than none.
            return np.repeat(y[:, None], 2, axis=1)

        if not self._shaper.identity or self._shaper.pending:
            dry_st, y = self._shape(dry_st, y[:n])
            if len(y) == 0:
                return _EMPTY_STEREO
        if not self._stereo_enabled:
            return np.repeat(y[:, None], 2, axis=1)
        return self._stereo.process(dry_st, y)

    def _shape(self, dry_st: np.ndarray, wet: np.ndarray) -> tuple:
        """The mask shaper, or — on the block after its controls return to
        their no-op values — what it was still holding, unshaped. Handing
        that back rather than dropping it keeps output count-aligned, which
        is what the positional dry/wet pairing depends on."""
        if not self._shaper.identity:
            return self._shaper.process(dry_st, wet)
        held_st, held_w = self._shaper.drain()
        return (np.concatenate([held_st, dry_st]),
                np.concatenate([held_w, wet]))

    def _collect_lag(self, dry_st: np.ndarray, wet: np.ndarray) -> None:
        """Gather windows for the lag measurement and apply the answer.

        Correlation runs on its own thread — it costs 5-12 ms, and spending
        that here empties the wet FIFO and inflates the dry backlog, which
        is permanent latency (audio/lag.py). Only this thread ever mutates
        _dry_work, so the answer is applied here rather than by the measurer.
        """
        if self._pending_lag is not None:
            lag, self._pending_lag = self._pending_lag, None
            self._mask_lag = lag
            if lag:
                # Delaying the dry stream by the lag makes every later
                # pairing correct with no per-sample bookkeeping: the head
                # of the FIFO is simply that much further behind.
                pad = np.zeros((lag, 2), dtype=np.float32)
                self._dry_work = np.concatenate([pad, self._dry_work])
                # and the same correction for the stream the user hears
                # below 100 % mix, applied on its own thread.
                self._mix_pad_pending = lag
            return
        if self._measuring:
            return
        if self._lag_est.exhausted:
            # Unknown, not zero. Assuming zero applies a correction of the
            # wrong size to both the mask and the mix; leaving it unknown
            # applies none and keeps the rebuild bypassed, which is the
            # audio you get without ever having measured. The UI reports it.
            self._lag_given_up = True
            return
        if not self._lag_est.push(dry_st.mean(axis=1), wet):
            return

        d, w = self._lag_est.take_window()
        self._measuring = True
        gen = self._lag_gen
        # Bound now, not read inside run(). A swap installs a fresh
        # estimator, so a late read would hand this window to the new
        # model's estimator — harmless (measure() is pure) but it makes the
        # thread's subject depend on when it happened to be scheduled.
        est = self._lag_est

        def run():
            try:
                lag = est.measure(d, w)
            except Exception:  # noqa: BLE001 — a failed measurement just
                lag = None     # means another window, never a dead stream
            if gen != self._lag_gen:
                # The model was swapped while this ran. The answer describes
                # a processor that is no longer installed, and the swap has
                # already cleared _measuring — touch neither.
                return
            if lag is not None:
                self._pending_lag = lag
            self._measuring = False

        threading.Thread(target=run, daemon=True).start()

    def _wet_stereo_proc(self, proc, down, up, block) -> np.ndarray:
        """A processor that consumes the stereo pair itself (ROADMAP A1).

        No downmix, so no image to rebuild — and no mid/side either: that
        filter's whole job is producing a mono, center-emphasised signal for
        a mono model, which is precisely what this path does not want.
        """
        # Dry paired by position, as on the mono path, for the mask shaper.
        # No lag measurement is needed here: a wants_stereo processor emits
        # count-aligned output (processors/base.py), so the FIFO alone lines
        # the two up. Fed and drained whether or not shaping is on, so
        # switching it on mid-stream finds the FIFO already in step.
        self._dry_work = np.concatenate([self._dry_work, block])
        if len(self._dry_work) > self._max_out:
            self._dry_work = self._dry_work[-self._max_out:]
        x = down.resample_chunk(block) if down is not None else block
        y = proc.feed(x)
        if up is not None and len(y):
            y = up.resample_chunk(y)
        if len(y) and self._bandlimit_enabled:
            y = np.stack([self._bandlimit.process(y[:, 0]),
                          self._bandlimit_r.process(y[:, 1])], axis=1)
        if len(y) == 0:
            return y
        n = min(len(y), len(self._dry_work))
        dry_st, self._dry_work = self._dry_work[:n], self._dry_work[n:]
        if not self._shaper.identity or self._shaper.pending:
            _, y = self._shape(dry_st, y[:n])
        return y
