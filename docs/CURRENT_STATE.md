# Current state — read this first

One page and kept short on purpose. `ROADMAP.md` (≈2,250 lines) and
`HANDOVER.md` (≈1,300) hold the full record, including corrections on top of
corrections. This page says what is true **now** and where to read further.
When it disagrees with them, check the dates; this page was written
2026-10-23.

## Where things stand

- **Released:** v0.1.3, Linux/PipeWire only.
- **`main`** (= `origin/main`, `8df4f83`) is 0.1.4 but untagged. The tag is
  blocked by total suppression: quiet input into `dpdfnet_hr` comes out
  silent (HANDOVER "What gates the 0.1.4 tag"; fix is ROADMAP B10).
- **This branch, `feat/separator-spike`:** 42 commits ahead of `main` and 8
  ahead of its own remote. Everything below is committed locally
  (`b2687aa`, `d77ac08`); nothing is pushed.

## The finding that matters

The shipped models are speech enhancers, so they keep anything voice-like.
A real separator is the only route to the product promise (ROADMAP §2).
One now runs live: **StemgenRT** (`stemgen`, ROADMAP A2).

Measured 2026-10-24 over the whole corpus (234 clip/ratio pairs):

| | voice gain (dSI-SDR) | voice damage | music left | stereo |
|---|---|---|---|---|
| `dpdfnet_hr` (default) | +6.1 dB | −3.0 dB, `vocal-loss` on 46 clips | **−41 dB** | collapsed |
| `stemgen` | **+7.9 dB** | **none** | −22 dB | **kept** |

On `nasheed` alone (130 pairs), `dpdfnet_hr` wins dSI-SDR (+7.7 against
+5.7). It also removes about twice as much music there.

**Neither model wins outright.** StemgenRT keeps the voice and the image;
`dpdfnet_hr` removes more music. StemgenRT's leftover music, about −20 dB
and −8 to −10 dB on its worst clips, is plainly audible. Which matters more
is a listening and product decision. StemgenRT costs 5.8 ms of latency and
2.3 ms per 20 ms block. Full table: ROADMAP A2.

## Listening and looking (2026-10-24)

Rendered to local disk under `~/.local/state/music-assassin/bench/listen/`:
- `audio_male/`: 8 male-lead clips, both ratios; input, `stemgen`,
  `dpdfnet_hr`, offline `ceiling`, ideal-mask `oracle` as WAVs.
- `female/audio/`: 12 female-vocal clips, same files.
- `video/male/`, `video/female/`: one mp4 per clip. Waveform and mel
  spectrogram for every stage, a model-vs-oracle map (red = leak, blue = voice
  cut), level curves, and one audio track per stage (mpv: `#` cycles).
  Make more with `scripts/visualize_stages.py`.

Girls' voices (ROADMAP A2): `stemgen` keeps all of them; `dpdfnet_hr` loses up
to 11 dB. Directions to try next: `docs/RESEARCH_IDEAS.md`. How the pipeline
got here: `docs/PIPELINE_HISTORY.md`.

## Work on this branch (2026-10-22/24)

- **Engine:** FIFO caps now follow the processor, and trimming keeps dry and
  wet in lockstep. Chunked separators were 84 % unprocessed before this.
  Test: `tests/test_engine_chunked.py`.
- **StemgenRT processor:**
  - `processors/stemgen.py`
  - `scripts/fetch_stemgen.py`, which installs it to the data dir; the model
    is bundled in the .deb (decision 2026-10-24) although its training data is
    non-commercial; see packaging/model-licenses/MIT-stemgen-rt.txt.
- **Suppression limit and smoothing for every model:**
  - Code: `audio/maskshape.py`, with power smoothing and a gain capped at 1.
  - UI: both are sliders on the panel; the CLI has `--limit-db` and
    `--smoothing-ms`.
  - Defaults: both off.
  - Measured effect: on `dpdfnet_hr`, a 20 dB floor ends pumping (67 → 0
    clips on `nasheed`) at the cost of 19 dB of music suppression. On
    `stemgen` they only hurt.
- **Stereo rebuild:** `MAX_GAIN` 8 → 1, which ends a +17 dB amplification
  of anti-phase content.
  - Committed as `b2687aa`. `85328cf` had retracted it, on a measurement taken under
    sweep load.
  - **Re-measured idle 2026-10-24:** the image is restored at −3.1 dB
    against −2.9 dB with the old clamp. Full suite passes.
  - Details in `RESEARCH_PRIOR_ART.md` §2.8, "Correction to the correction".
- **Bench:** `shape_mask()` had its alignment inverted, which voids every B4
  sweep number before 2026-10-22. Retraction in HANDOVER.
- **Docs:**
  - `RESEARCH_PRIOR_ART.md`: prior art and a pipeline review.
  - `LIVE_SFX_HANDOVER.md`: sound-effects work from the offline app.

## Output follows the system (2026-10-26)

The Output dropdown and the saved `output_name` are gone. The app outputs to
whatever the system's output is, and `routing.check()` follows it when it
changes from outside (system menu, Bluetooth reconnect). Reason: a picker
that can disagree with the system is a second source of truth, and the
reported failure was audio stopping after the output was changed behind the
app's back. Old `output_name` entries in settings.json are ignored.
`test_routing_dry` needs the app closed to run.

## Keep sound effects, live (2026-10-27)

There is now a **"Keep sound effects" switch** on the Processing card, and a
model behind it that can honour it: **MRX** (MERL cocktail-fork, int8 ONNX,
MIT, 31 MB), the first three-way dialogue/music/effects separator here.
`scripts/fetch_mrx.py` installs it; `processors/mrx.py` runs it;
`models/mrx.json` holds every number below.

Why a model and not a knob: every other processor emits one scalar per bin
saying how speech-like it is, and drums and effects are adjacent in that one
ranking, so no monotonic post-processing of it can keep one and drop the
other (`IDEAS.md` 1.1). A separate effects mask is a second axis.

- **Cost.** RTF ~0.10 on one thread (83 ms of CPU per second of audio, flat
  from 1 s to 8 s chunks). Choosing `mrx` costs **1805 ms of latency** at the
  2000 ms default; the switch itself only adds one iSTFT per resolution,
  about 6 % of a window. MRX replaces `dpdfnet_hr` rather than running
  beside it, since one pass yields both stems.
- **The chunk is the latency and the quality.** Error against a full 30 s
  pass falls about 3 dB per doubling, and the effects stem trails the
  dialogue stem by 3–5 dB throughout. Nothing inside a live budget
  reproduces the 30 s result. 2000 ms is the shortest chunk whose effects
  stem errs by less than its own energy, which is why it is the default.
- **The real limit is the training domain, not the plumbing.** MRX is
  trained on DnR: film speech, score, foley. On 4 b3 male-lead clips with
  **no effects in them at all**, the effects stem came out at −6.2 dB
  relative to dialogue raw and −10.6 dB after over-subtraction, and −0.7 dB
  on the worst clip. All of it is leaked music. Offline, on cinematic
  content, the same model measures −21.3 dB.
  **So: on for film and TV, off for music with singing.**
- **Over-subtraction** of the music stem from the effects stem is on by
  default, as it is offline. Done in the model's own three STFT domains
  instead of a separate analysis: −4.4 dB on these clips against the offline
  implementation's −4.9 dB, which cross-validates the shorter route.
- **Not bundled in the .deb.** DnR's component-corpus terms are unchecked —
  the same open question as StemgenRT's, and it must be settled first.
- Tests: `tests/test_mrx.py` (windows, count alignment, that the switch
  changes the audio by adding a stem rather than rescaling) and
  `tests/test_cstft.py` (the torch-free STFT against torch, because MRX's
  int8 graph depends on the `normalized=True` scale).

## Next, in order

1. **Stemgen ships as is** (user decision 2026-10-24): selectable in the picker,
   `dpdfnet_hr` stays default, bundled in the .deb (decided 2026-10-24; training data is
   non-commercial, flagged in its licence file). Cutting its music leak
   is next version's work (RESEARCH_IDEAS §1).
   Done: the `MAX_GAIN = 1` fix (`47a086d`). The re-measurement shows it costs
   0.2 dB of image and removes a +17 dB burst. A level-referenced
   denominator is a separate, later improvement (§2.8).
2. **Choose the default model by ear.** The sweeps are in (ROADMAP A2):
   StemgenRT keeps the voice, `dpdfnet_hr` removes more music. Listen to both
   on a few `nasheed` and `male_lead` clips, using `bench_quality.py
   --dump-audio`.
3. **Listen.** The by-ear checks still open:
   - StemgenRT versus `dpdfnet_hr`;
   - floor and smoothing on `dpdfnet_hr`;
   - HANDOVER gates 3 and 4.
4. **Check whether players compensate for reported latency** (mpv, Firefox,
   Chromium) before building anything that needs it. This machine runs
   PipeWire 1.0.5, so declaring latency would need a native `pw_filter`.
   Loopback `ProcessLatency` needs 1.4 or later.
5. **Sound effects:** the MRX route is built (above). What is left is a
   listen on real cinematic content — every number here is from music
   clips, which is MRX's worst case — and a decision about whether 1805 ms
   is acceptable, which is item 4. `LIVE_SFX_HANDOVER.md` §4 still ranks the
   cheaper routes; a music-presence gate remains the only one that costs no
   latency.
6. Tag 0.1.4 once B10 or a StemgenRT default removes the suppression
   blocker.

## Decisions only the user can make

- **Q3, latency:** is a high-latency "movie mode" acceptable at all? It
  gates SFX route (c).
- **Musical noise:** how much is an acceptable price for a kept syllable?
- **StemgenRT's licence:** its training data is non-commercial (MUSDB18-HQ,
  MoisesDB). That decides whether it can be bundled.

## Working here: things that bite

- **Never point sweep logs at the sftp mount.** The first write after a long
  quiet phase fails, and the run dies with an empty log. Log to local disk;
  CSVs written at the end are fine.
- **Imports over the mount are slow:** about 2 minutes for `bench_quality`.
- **The scratchpad is wiped on reboot or date change.** Long runs must
  `setsid nohup` and write their results outside it.
- **`git commit` is flaky over the gvfs mount** (`could not read commit
  message`). `-m` and `-F -` fail reliably; `-F <file>` with the message on
  local disk works most of the time and failed once in six, where an
  immediate retry of the same command worked. Retry once before reaching for
  HANDOVER's `commit-tree` + `update-ref` route. The index stays staged on
  failure, so nothing is half-committed.
- **`test_stereo_rebuild` is timing-based.** It fails under CPU load *and*
  when run from the sftp-mounted tree. A −235.9 dB "side retained" means the
  rebuild never engaged, not that it collapsed the image. For an A/B,
  compare local copies on an idle machine.

## Reading order

1. This page.
2. ROADMAP §2 (why separation).
3. ROADMAP A2 and B4, latest notes first.
4. HANDOVER "What landed 2026-10-22".
5. `RESEARCH_PRIOR_ART.md` §2.11.
6. Everything else as needed.

If the next step is not working and you want the option space rather than the
plan, read `IDEAS.md`: every idea raised for this project, shipped or dead or
never tried, grouped by the problem it addresses, with four principles at the
top that each prune a whole family of approaches.
