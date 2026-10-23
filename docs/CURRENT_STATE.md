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
  ahead of its own remote. On top of that, the large body of work below is
  still **uncommitted**.

## The finding that matters

The shipped models are speech enhancers, so they keep anything voice-like.
A real separator is the only route to the product promise (ROADMAP §2).
One now runs live: **StemgenRT** (`stemgen`, ROADMAP A2).

`male_lead` sweep, 16 clip/ratio pairs:

| | voice gain (dSI-SDR) | voice lost | music left | stereo |
|---|---|---|---|---|
| `dpdfnet_hr` (default) | +0.3 dB | −8.5 dB | −48 dB | collapsed |
| `stemgen` | **+9.9 dB** | **0 dB** | −24 dB | **kept** |

StemgenRT costs 5.8 ms of latency and 2.3 ms per 20 ms block. The cost is
that it leaves twice as much music, in dB. Two runs decide whether it
becomes the default:
- the whole-corpus comparison;
- the `nasheed` sweep.

Both write to `~/.local/state/music-assassin/bench/stemgen/`.

## Uncommitted on this branch (2026-10-22/23)

- **Engine:** FIFO caps now follow the processor, and trimming keeps dry and
  wet in lockstep. Chunked separators were 84 % unprocessed before this.
  Test: `tests/test_engine_chunked.py`.
- **StemgenRT processor:**
  - `processors/stemgen.py`
  - `scripts/fetch_stemgen.py`, which installs it to the data dir; the model
    is not bundled because its training data is non-commercial.
- **Suppression limit and smoothing for every model:**
  - Code: `audio/maskshape.py`, with power smoothing and a gain capped at 1.
  - UI: both are sliders on the panel; the CLI has `--limit-db` and
    `--smoothing-ms`.
  - Defaults: both off.
  - Measured effect: they help `dpdfnet_hr` and hurt `stemgen`.
- **Stereo rebuild:** `MAX_GAIN` went from 8 to 1. It was amplifying
  anti-phase content by +17 dB.
- **Bench:** `shape_mask()` had its alignment inverted, which voids every B4
  sweep number before 2026-10-22. Retraction in HANDOVER.
- **Docs:**
  - `RESEARCH_PRIOR_ART.md`: prior art and a pipeline review.
  - `LIVE_SFX_HANDOVER.md`: sound-effects work from the offline app.

## Next, in order

1. **Read the two sweep results** and decide the default model. If StemgenRT
   holds up, the B4/B9/B10 polish on `dpdfnet_hr` drops in priority.
2. **Listen.** The by-ear checks still open:
   - StemgenRT versus `dpdfnet_hr`;
   - floor and smoothing on `dpdfnet_hr`;
   - HANDOVER gates 3 and 4.
3. **Check whether players compensate for reported latency** (mpv, Firefox,
   Chromium) before building anything that needs it. This machine runs
   PipeWire 1.0.5, so declaring latency would need a native `pw_filter`.
   Loopback `ProcessLatency` needs 1.4 or later.
4. **Sound effects:** `LIVE_SFX_HANDOVER.md` §4 ranks the routes. The
   cheapest is a music-presence gate.
5. Tag 0.1.4 once B10 or a StemgenRT default removes the suppression
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
- **`git commit -m` and `-F -` fail over the gvfs mount** (`could not read
  commit message`). `git commit -F <file>` works if the file is on local disk,
  off the mount — verified four times on 2026-10-23, and simpler than the
  `commit-tree` + `update-ref` route HANDOVER describes.
- **`test_stereo_rebuild` is timing-based** and fails under CPU load. Its
  result means nothing while a sweep is running.

## Reading order

1. This page.
2. ROADMAP §2 (why separation).
3. ROADMAP A2 and B4, latest notes first.
4. HANDOVER "What landed 2026-10-22".
5. `RESEARCH_PRIOR_ART.md` §2.11.
6. Everything else as needed.
