# Handover — current state

> **New here? Start with `docs/CURRENT_STATE.md`** — one page on what is true now; this file is the full record.

Updated 2026-08-20. **This file is deliberately short.** It covers only where
things stand *right now* and what to do next. The plan, the reasoning, the
measured findings and the list of dead ends all live in
[`docs/ROADMAP.md`](docs/ROADMAP.md) — read that before re-deriving anything.

## Where the code is

**Both feature branches are pushed as of 2026-08-20. The 0.1.4 tag is still
not cut.**

| Branch | Contents | State |
|---|---|---|
| `main` | 0.1.4, green on the offline suite | **25 commits unpushed** |
| `feature/stereo-output` | B3 stereo rebuild, `wants_stereo`, C1, C2, C3, C4, C7, C8, E1 | **pushed**, 38 ahead of `origin/main` |
| `feat/separator-spike` | A1 spike + the SpleeterProcessor and its sweep, then B6/B7/C9, then B8/B6b/C10, the C11 panel rebuild, and the 2026-08-20 work below | **pushed**, 58 ahead; contains `feature/stereo-output` |
| `feature/windows-packaging` | installer scaffolding | ~25 commits stale; cherry-pick forward, never merge (D3.1) |

`origin/main` is **still at `06f0ac8`** and no tags have moved — the last
published release is **v0.1.3**. `main` itself has never been pushed, so the
25 commits on it reach the remote only through the two feature branches.

**CI ran for the first time and passed on both branches**, with no fix-up
needed to the apt/pip step. Read what green means before trusting it: the
seven hardware-free tests run, but both model tests report `SKIPPED (models
not installed)` because the ONNX files are not in the repo. CI covers
routing, recovery, the status line and capture diagnosis; it exercises **no
processor at all**. And see the flaky-test note under the tag gates —
`test_stereo_rebuild` fails intermittently, so a green run is not proof.

Credentials now exist on the dev machine (`gh auth login` + `gh auth
setup-git`, as `A-Ahmad-02`). Push the parent branch first, or the commits
arrive with no branch name on them.

**`git commit -m` and `-F` fail in this checkout** — `fatal: could not read
commit message: No such file or directory`. It is not the message: git
writes `.git/COMMIT_EDITMSG` and cannot read it back over the gvfs/sftp
mount. It worked earlier the same day and then stopped, so it is the mount,
not the repo. The workaround that does work is `git commit-tree` +
`git update-ref`; committing from the machine that owns the repo avoids it.

**Partial workaround, and a correction to it (2026-10-23/24).** `-F -`
(stdin, via heredoc) fails reliably. `git commit -F <file>` with the message
on local disk, off the mount, succeeded five times out of six in two
sessions — but **the sixth failed with the identical error, and an immediate
retry of the exact same command succeeded.** So the fault is transient mount
I/O, not the message path, and an earlier version of this note overstated
the fix. In practice: write the message to a file outside the mount, use
`-F`, and **retry once on failure** before reaching for `commit-tree` +
`update-ref`. Nothing is left half-committed when it fails — the index stays
staged.

**The `.deb` cannot be built over the gvfs mount either**, and the errors
point nowhere near packaging. PyInstaller's `--add-data` rejects any source
path containing a colon, which every gvfs mount point has
(`sftp:host=...`), and `objcopy` fails with "Operation not supported"
rewriting ELF sections on the mount. `scripts/build_deb.sh` now takes
`BUILD_DIR` from the environment so the scratch tree can live on local disk,
accepts `ffmpeg` when ImageMagick is absent, and invokes PyInstaller as
`python -m` so a venv created at another path still works. Build it with a
colon-free path to the checkout (a symlink is enough):

```
BUILD_DIR=/tmp/ma-build /path/without/colons/scripts/build_deb.sh
```

## What gates the 0.1.4 tag

**Updated 2026-08-20 after a second listening session.** One gate closed, one
symptom turned out to be misread, and two real defects surfaced while chasing
it. Current state of the four:

| gate | state |
|---|---|
| 1. Doubling with the stereo rebuild on `dpdfnet_hr` | **CLOSED** — confirmed gone by ear, 2026-08-20 |
| 2. Audio sometimes silent from idle | **not reproduced**; what reproduces is *late*, which B7 explains, plus total suppression (below) |
| 3. Does the rebuild bring music back | **unblocked by 1, still unanswered** |
| 4. Does the mix slider blend rather than echo (B8) | **still unanswered** |

**The new blocker is not in this list.** A `.deb` of 0.1.4 was built and
verified on 2026-08-20 (package well-formed, four models bundled,
`speechdenoiser` correctly excluded, binary answers `--help`) and is sitting
in `dist/` unreleased. What stops it shipping now is the suppression defect
in "the lip-sync reports were three different things" below, plus a test that
cannot be trusted to tell you the suite is green.

The original four defects, for context — reported from the first real run
(2026-08-19, from the repo, not the `.deb`); three were fixed that day and
the fourth, the dry/wet skew, the next:

**1. Doubling with the stereo rebuild on `dpdfnet_hr` — RESOLVED 2026-08-20.**
Confirmed gone by ear in the app. The text below is kept because the
readings it describes are still the right ones to quote if it returns.
B6's fix is confirmed working on `dtln` (8 ms lag) and in the offline
regression test with a processor lagging 2400 samples, which the estimator
measures to 0.1 ms. So the mechanism is right and something about
`dpdfnet_hr` in the live app is not.

Leading suspects, in order: the runtime measurement lands on a wrong value
for real content at stream start; or the residual is the rebuild's own
framing rather than the model lag. The estimator's two ways of failing
quietly have since been closed (ROADMAP B6b), so a bad measurement can no
longer disguise itself as a good one — but the reading still decides which
of the remaining suspects it is.

**Read the lag while you can hear it.** It is on the status bar
(`… · 50 ms · lag 50.0 ms`) and in `details ▸`, and it says one of three
things, which are different answers:

| reading | meaning |
|---|---|
| `50.0 ms` | measured, correction applied — the residual is something else |
| `measuring…` | not yet decided; it needs 1.5 s of non-silent audio |
| `unmeasured (rebuild bypassed)` | no peak worth believing; nothing corrected, rebuild off |

Note what `mix:` says too. Below 100 % it used to be a second, independent
source of doubling — fixed the same day (ROADMAP B8), and worth confirming
by ear.

**2. One unexplained report: audio sometimes does not play at all when
starting from idle.** Not the old broken `pin_stream()` — that is fixed on
this branch and the run was from the repo. Startup lag (B7) explains *late*,
not *silent*.

**When it happens, read the status bar and the meter.** Every path that
stops the audio writes a distinct message, so it identifies itself, and the
message now stays on screen for 20 s instead of being overwritten by the
next tick. The meter draws the captured input above the emitted output
(C10), which splits the one case the messages cannot name:

| meter | meaning |
|---|---|
| `in` moving, `out` flat | the engine or the mix — not capture |
| both flat | capture: nothing is arriving |
| both moving, nothing audible | routing past our output — the "healthy but silent" case |

The status-line messages:

| message | meaning |
|---|---|
| `feedback loop detected (capturing our own output) — turned off` | C8 fired — possibly correctly, possibly a false positive |
| `capture is connected to the wrong device — turned off` | C8, wrong capture peer |
| `audio device disappeared — turned off, switch back on to rebuild` | trap sink gone |
| `audio stream died, restart failed: …` | PortAudio stream lost |
| `Filtering → … · healthy` **while silent** | the interesting case: healthy stream, no sound — routing, not the engine |

**By-ear: which model** — partly done and **partly confounded**, see below.

**3. By-ear: does the stereo rebuild bring music back** — still unanswered,
and now blocked behind item 1.

**4. The dry/wet mix was misaligned by the processor's internal lag** —
**fixed 2026-08-19 (B8)**, and the last thing it needs is ears. B6 padded the
worker's dry copy, which feeds the stereo mask, and left the callback's
alone, so below 100 % mix the blend was two streams 50 ms apart. Both are
padded now: measured 0.0 ms skew with the fix against 49.9 ms without it. The
mix slider should now work as the tool it was meant to be — trading vocal
damage against music removal — which is worth confirming before B4 starts
producing candidates to compare with it.

## What to do next

> **Current order — revised 2026-10-23. The items below keep their numbers,
> because other items cross-reference them, but the ordering and much of the
> status is superseded by ROADMAP §9's "Current order" — read that first.**
>
> A lot landed in the working tree on 2026-10-22/23 and **none of it is
> committed**: the engine now sizes its FIFO caps from the processor's
> `latency_samples` (the review's §2.2 fix), trims the paired dry with the wet
> (§2.3), measures per-processor lag in a new `audio/lag.py`, and has a
> plumbing test (`tests/test_engine_chunked.py`) wired into `run_tests.sh`.
> StemgenRT is wrapped as the `stemgen` processor and benched here at RTF
> 0.094 / 5.8 ms, with 9.49 dB vocals SDR on one nasheed clip against 2.10 dB
> for the untouched mix. B4's floor and smoothing moved out of
> `bench_quality.py` into `audio/maskshape.py` with their own tests.
>
> So items 5 and 7 are largely *built* rather than pending, and item 5's own
> "immediate next step" is marked superseded. What is still open: StemgenRT on
> **spoken dialogue** (untested, ~1 hour, gates everything else about it), the
> corpus sweep, the mask-shaper sweep on sparse material, the non-commercial
> **training-data licence** that blocks bundling it, and the latency-reporting
> experiment. Read the scope correction at the head of "The permissiveness
> knob" below before touching B4, and `docs/LIVE_SFX_HANDOVER.md` before
> touching SFX — offline add-back works and was preferred by ear, which turns
> SFX from an open question into a routing choice. Effort estimates and the
> divergences from the review's own suggested order are in ROADMAP §9; the
> review itself is `docs/RESEARCH_PRIOR_ART.md`.

Reordered 2026-08-20. The first three come out of the listening session and
displace what used to be first here. **Item 5 updated 2026-09-08** — see
"The permissiveness knob" below for what actually happened to B4.

**1. Fix the flaky `test_stereo_rebuild`.** Cheapest item on the list and it
gates the credibility of everything else: while it fails intermittently, no
suite run proves anything, including the ones the tag would be cut on.

**2. B10 — normalise what the model is fed.** Promoted from "measurement
hygiene" to a shipping defect: a quiet source into `dpdfnet` produced
`input −37.3 dB / output −87.4 dB`, i.e. total suppression, and there is
still no input trim anywhere in the chain. A slow AGC ahead of the model with
the inverse gain after it is a small, testable change, and the sweep is
already specified in ROADMAP B10.

**3. Finish the by-ear gates that are still open.** Two remain and both are
minutes of work: does the stereo rebuild bring music back (unblocked now that
the doubling is gone), and does the mix slider blend rather than echo (B8).
While there, read **output backlog** in `details ▸` — if it ever shows
`SATURATED`, the drift question is answered yes and needs its own item.
**Do it without changing model mid-session**, and then again *with* a
switch: defect 3 below made a live switch bake in ~140 ms permanently, and
separating that from real drift is the whole point of the reading.

**Watch `fallbacks` while you do it.** It is the counter the 2026-08-21
regression should have been caught on and was not: dry/wet alternation is
audible as a pulsing beat on every model, and neither `xruns` nor
`stream_ok` moves when it happens. `added latency` climbing with session
length is the open ratchet, not a new fault.

**4. Re-rank the models blind** through `scripts/ab_listen.sh`, not through
the app. Every ranking so far has been sighted, and the 2026-08-20 session
placed `dpdfnet` third despite it being the only net-harmful model — which is
the kind of error a blind test exists to catch.

**5. B4 — constrain the mask in time. In progress, blocked on a real bug —
read "The permissiveness knob" below before touching this.** `shape_mask()`
exists in `tests/bench_quality.py` (uncommitted) with the exponent and
`floor_db` built, verified and swept on two corpora — `dpdfnet_hr` is
conclusively ruled out for this mechanism (worse noise at every setting, on
both genres, even where its baseline is strong), `gtcrn`/`dtln` show a real
sweet spot whose *cost* depends on how much music is in the source, not
just which model is loaded. `attack_ms`/`release_ms` — the half that
actually targets "collapses faster than a syllable" — hit three bugs in a
row during verification, the last one unresolved (a stale smoothed value
persisting across frames, producing an amplification burst nowhere near the
frame that caused it) and is marked unsafe in the code.
**Superseded as the immediate next step, 2026-10-18.** The unresolved bug
now has a fix (smooth the two powers, not the ratio — ROADMAP B4's
2026-10-18 note), and what goes ahead of it is the two engine bugs in
`docs/RESEARCH_PRIOR_ART.md` §2.2/§2.3, which gate any separator work. The
`floor_db` listening set was rendered in full and remains a valid test of
*pumping*; it is not a test of SFX recovery. Read the scope correction at
the head of "The permissiveness knob" below before acting on anything in
that section. Full design context in ROADMAP B4.
**Built, 2026-10-22** — see "What landed 2026-10-22". Smoothing and the floor
now run in the engine for every model, and both are on the panel. The old
probe's alignment was inverted, so the sweep numbers in "The permissiveness
knob" are void until re-measured.

**6. B9 — give the top octave back.** The measured damage is concentrated
there: whole-corpus per-band vocal loss runs −4.8 / −4.3 / −7.0 / −8.6 /
**−9.1** dB from sub to air, and −13.0 in the air band on `male_lead`.
Blending dry back above a 6–8 kHz crossover is one crossover and one gain,
and `band_damage` measures it directly. 48 kHz models only — `dtln` and
`gtcrn` have nothing up there to restore.

**7. C12 — expose what 5 and 6 add.** Suppression floor and smoothing as
sliders that work on every model (today only `speechdenoiser` has a limit),
"keep air above N kHz", and then presets — `voice first` / `balanced` /
`music first` — which is how the standing product judgement becomes
something a user can state. Right now the only way to say "protect the
voice" is to pull a mix fader that also changes the output level. The panel
has room for all of it since C11.

B10 is **item 2**, not last — it was promoted on 2026-08-20 when it stopped
being a measurement caveat and started silencing a listener's audio. Its
other claim still stands: it may also be the missing explanation for E2's
unaccounted 34 dB swing.

Items 5–7 are measurable offline before anyone listens, which is the order
that has worked so far: sweep, then ears, then ship. Items 1–4 are not — they
are the ones that need the app, the ears and a working test.

## Windows — planned 2026-08-20, not started

Written out in ROADMAP D3 (phased) and D4 (licensing, now settled). Three
things worth carrying without opening it:

- **The installer was never the blocker.** `packaging/windows/` has existed
  since 2026-08-13 and has still never been compiled or run; what is missing
  sits underneath it. D2 landing means the routing backend is now one new file
  written against `backends/base.py`, not a fork of three.
- **Two claims in the old plan were wrong**, both corrected in D3. sounddevice
  0.5.5 has no WASAPI loopback flag — `WasapiSettings` takes
  `exclusive, auto_convert, explicit_sample_format` and nothing else — and
  loopback would be the wrong tool regardless: it *taps* the signal where this
  app has to *insert* into it, so the original audio would keep playing
  underneath the processed one. A virtual output device is therefore required
  for v1; the only alternative is an APO, which is the C++ port.
- **VB-CABLE is settled.** The public build downloads it from VB-Audio with a
  pinned SHA-256, verifies, extracts and runs it elevated — so our artifact
  ships none of their bytes, nothing is redistributed, and five of the seven
  manual steps disappear along with both of the silent-failure traps. A
  separately-named bundled build stays for internal use only; the name does
  not change what publishing it would mean.

Two decisions still open, both cheap: whether `paths.py` should use
`%LOCALAPPDATA%` on Windows instead of XDG (D3.2), and whether
`vb-audio-permission-email.md` is worth sending at all now that the download
path makes bundling unnecessary.

Start at D3.1. Nothing here needs a Windows machine until D3.3.

## The permissiveness knob (B4) — investigated 2026-08-21 to 2026-09-08, NOTHING COMMITTED

> **Retraction, 2026-10-22 — every number in this section was measured
> misaligned.** `shape_mask()` estimated the lag correctly but applied it the
> wrong way round. `lag > 0` means wet *trails* dry (`align()`'s
> convention); the code then took `dry[lag:]`, which advances dry. So each
> frame's `|Wet|/|Dry|` was taken against dry from 2 × lag away: about
> 100 ms on `dpdfnet_hr` (50 ms lag), 32 ms on `gtcrn` and 16 ms on `dtln`,
> all against a 512-sample (10.7 ms) analysis window. That covers the
> exponent tables, "`dpdfnet_hr` is conclusively ruled out", and the 3.48×
> blowup attributed to smoothing; all of them need re-measuring. It does not
> cover the identity checks, which returned wet untouched, or the scope
> correction below, which is an argument rather than a measurement.
> Verified on synthetic wet lagged 0/480/2400/−300 samples: with the fix, a
> floor that should change nothing gives 6e-8 error; the old alignment gave
> 0.22 at 480 samples. The code that replaced this probe is
> `audio/maskshape.py`, described in "What landed 2026-10-22".

> **Scope correction, 2026-10-18 — this section conflated two goals, and the
> conflation is this document's, not ROADMAP's.** The stated aim was to loosen
> the music/vocal boundary so sound effects return. ROADMAP B4 is scoped as a
> *quality* lever (pumping, `vocal-loss`, a gain collapsing faster than a
> syllable) and never claimed SFX; the SFX goal was attached to it here.
>
> It was never reachable this way. The model emits one scalar per bin — how
> speech-like — and the exponent and `floor_db` are monotonic functions of it,
> so they preserve the model's own ranking of bins. Admitting an SFX bin
> admits every bin ranked more speech-like, and drums rank beside SFX because
> they share the features the model keys on: broadband, transient,
> inharmonic. The discriminating information is gone before the knob runs.
> ROADMAP §11 already recorded the measured consequence — pure-DSP SFX
> add-back plateaus at ~8 % music leakage, "a semantic classifier is the only
> thing that works" — and this investigation rediscovered that plateau without
> recognising it. **Read §11 before re-opening any of this.**
>
> What survives, and is worth shipping: `floor_db` as the C12 suppression-floor
> control on every model, and the timing half once the powers fix lands, both
> as *quality* controls. What does not: the exponent on `gtcrn`/`dtln` (effect
> flips with how much music the source has, and by ear it could not fix the
> baseline), and any reading of this section as addressing SFX. Keeping SFX is
> ROADMAP A4's problem — see its 2026-10-18 note, and the offline app's
> measured handover in `docs/LIVE_SFX_HANDOVER.md` (2026-10-23).

Everything in this section lives only in an uncommitted working-tree change
to `tests/bench_quality.py`. No app code touched. Read this before touching
B4, B9 or C12 — it changes what "build B4" actually means.

**Where this came from.** After the switch-path fixes, the user asked for a
"permissiveness bar" — not the mix slider (that blends dry back in
broadband, uniformly), something that edits *the filter's own decision*: let
more vocal through even if some music leaks, without the result getting
noisier. That request is ROADMAP B4, already specced (`release_ms`,
`attack_ms`, `floor_db`, all in the mask domain `audio/stereo.py` already
derives). This section is the record of actually building and testing it.

### The mechanism: a correction, not a reconstruction

`shape_mask()` recovers the chain's own per-bin decision as `|Wet|/|Dry|`,
reshapes it, and reapplies it to `wet` as a multiplicative correction plus a
dry-sourced residual for bins the model killed outright — it does **not**
resynthesise output from the mask. The first version did exactly that
(`istft(shaped_mask * Dry)`) and was wrong: measured on dtln at **identity**
settings (before the knob did anything), dSI-SDR went from +1.07 to −3.26
and SAR from 2.75 to −1.75. None of the four shipped models is the pure
magnitude mask that formulation assumes — dtln's second stage refines in the
time domain, dpdfnet_hr modifies phase — so rebuilding from `|Wet|/|Dry|`
throws that away. The correction form is exact at identity by construction
(verified: 0.0 max error, even against a synthetic wet signal deliberately
constructed so the mask formulation cannot describe it).

**One real bug already fixed in the exponent itself.** `mask ** γ` for
`γ < 1` pulls every bin toward 1 *from both directions* — it also pulled
down bins the chain had *boosted* (mask > 1: band-limit filter, resample
ringing), which is backwards for a control whose whole job is to remove
less. Measured cost on dtln/`male_lead`: 1.3 dB of vocal retention given up
for nothing. Fixed to act only where `mask < 1`.

### `male_lead` sweep (8 clips, dense mix) — the exponent's best case

| γ | dtln vocal_ret | dtln musNoise | gtcrn vocal_ret | gtcrn musNoise |
|---|---|---|---|---|
| 1.0 | −1.57 | 4.19 | −2.85 | −15.21 |
| 0.7 | −1.33 | 3.92 | −2.37 | −13.67 |
| 0.5 | −1.09 | 3.73 | −1.93 | −12.49 |
| 0.3 | −0.71 | **1.48** | −1.34 | −10.07 |

Every band improves monotonically on both models, and noise *falls* — a
near-free win in this category. `dpdfnet_hr` does not share it: musNoise
gets **worse at every γ tested** (6.81 → 8.30 → 14.56 → 13.46), no interior
improvement on SAR/dSI-SDR anywhere. Left open at the time: is that because
`male_lead` is `dpdfnet_hr`'s known-worst category, or the mechanism itself.

### The second corpus: `nasheed`, 65 clips, built and merged

Per the user's instruction to also test against the second corpus at
`~/Music/MusicAssassin/Corpus-Nasheed` (72 raw YouTube-sourced tracks,
vocal-forward devotional music, sparse backing — a genuinely different genre
from the anime-OP corpus). Screened first with `scripts/corpus_excerpt.py`'s
existing acapella guard before spending any demucs time: **65/72 passed, 7
refused** as vocal-only or spoken-intro (two read −40.5 dB and −29.1 dB
low-end, clearly not song mixes at all) — the exact mistake that guard was
built to catch after a prior session fed 91 acapella files into this same
pipeline. Built with `htdemucs`, ratios `1.0,2.0` (matching the existing
corpus), merged into the same manifest as a new `nasheed` category rather
than a separate corpus file. **Corpus is now 122 items across 8 categories,
`nasheed` the largest at 65.** Zero demucs failures across all 195
separation passes. The build itself took ~13 hours of real wall time (an
sftp mount dropped and recovered mid-build; the harness checkpoints after
every clip specifically so an interruption costs nothing — it did not this
time either).

### `nasheed` sweep — settles `dpdfnet_hr`, complicates `gtcrn`

**`dpdfnet_hr` resolved, cleanly, the wrong way.** Its `nasheed` baseline is
*strong* — 142%/51% of the offline ceiling on music/dSI-SDR, nothing like
`male_lead`'s weak baseline — and musNoise **still** worsens monotonically
at every γ (−5.58 → −0.80 → 3.08 → 6.31), with SAR/dSI-SDR flat-to-falling
throughout. This is not a bad-category artifact. It is the mechanism itself
failing on this architecture, independent of genre. **Conclusion: do not
offer this mechanism on `dpdfnet_hr` at all.**

**`gtcrn`'s vocal-retention story generalises; its noise story reverses.**
Per-band damage and vocal_ret still improve monotonically to γ=0.3, more
cleanly than on `male_lead` even (no reversal). But musNoise runs
**backwards**: −10.14 (best, γ=1) → −7.31 → −6.05 → −3.49 (worst, γ=0.3).
The failure tags explain it precisely — this is not the `male_lead` story of
smoothing a harsh artifact into a quieter one; it is a clean `pumping`
(67→4) for `leak-burst` (7→98/130) trade. Sparse backing has little
over-suppression left to recover, so permissiveness mostly unmasks *real*
residual music, which SIR (11.38→6.88) and `music_supp` (−25.60→−11.18)
both confirm mechanically. **The calibration problem has two independent
axes, not one**: which model (`dpdfnet_hr`: never), and how much music is
actually in the source (a live app cannot know this in advance, so a single
fixed γ is either too permissive on dense mixes or too leaky on sparse
ones). `dtln` was not swept on `nasheed` — only `gtcrn` — so its match to
this pattern is an extrapolation from `male_lead` similarity, not a
measurement.

### By ear: the numbers were an incomplete guide, twice over

Two clips checked by ear — `CodeGeassOP1` (`male_lead`) and `A Child's
Prayer` (`nasheed`), both ratios, `dtln`/`gtcrn` × γ={1, 0.7, 0.5}.

**The shipped baseline itself sounds bad.** `dtln+mask1` and `gtcrn+mask1`
— today's unmodified output, no knob touched — were both called "noisy" on
`CodeGeassOP1`, with "a constant buzz... and some musical notes." Only
`ceiling` (offline separator) and `oracle` (ground-truth cheat) sounded
clean. **No setting of the exponent can fix this**, because it only
reshapes a static per-frame decision the model already made — it cannot
touch *how fast* that decision moves, which is the actual defect ROADMAP B4
names: "a gain that collapses faster than a syllable."

**The exponent's effect on vocals did not match the aggregate metric.** At
`a200` specifically, `0.7`/`0.5` on both models were "lots of music slipping
through... very noisy," with vocals *not* clearly better — sometimes "more
muted" than at `mask1`. The aggregate numbers (averaged across `a100` and
`a200` together) hid this: if `a200` is dramatically worse and `a100` only
mildly worse, the average still reads as a real improvement. **Every sweep
number reported above and in earlier sessions is a two-ratio average and
none of it has been checked split by ratio.**

**`A Child's Prayer` (nasheed) partly contradicted the `nasheed` numbers
too.** `gtcrn`'s "noisy" complaint specifically *disappeared* at `0.7`/`0.5`
— the opposite of what the aggregate musNoise trend for `nasheed` predicted.
`dtln` stayed noisy and leaky throughout. Two clips, two different pictures,
on top of the numeric average already being an unreliable guide — the
by-ear check is not confirming the sweep here, it is finding real per-clip,
per-model variance the sweep's averaging structurally cannot see.

### The pivot: B4's *other* half — three bugs, none shipped

The user's read of all this, verbatim: **"process these first, since noise
needs to be removed and the filter that is supposed to be a permissivity
filter does not feel like that too much."** That pointed at ROADMAP B4's
`attack_ms`/`release_ms` — constraining *how fast* the mask may move, the
part that actually addresses "collapses faster than a syllable," as opposed
to the exponent/floor which only reshape a value that already moved too
fast. Implemented as one-pole ballistics per bin, composed underneath the
exponent/floor (smooth first, reshape what survives). **Three distinct bugs
surfaced in verification, in order, none reaching real audio for the user:**

1. **Identity-breaking gain collapse.** The smoothed value overwrote the raw
   `mask` variable before the gain correction, so `gain = want/mask`
   collapsed to ≈1 whenever exponent/floor were untouched — silently
   cancelling the smoothing entirely. Caught by a synthetic step-response
   test (a hard attenuation step, checking RMS just after the step): release
   measurably did nothing when it should have visibly lagged. Fixed by
   keeping the raw mask separate from the smoothed target throughout.

2. **A denormal-float stall.** `release_ms` lets the smoothed state decay
   geometrically toward near-zero forever, and float32 subnormal arithmetic
   is 10–100x slower on most CPUs. One real clip that should have taken
   seconds stalled past 90 s (the same clip processed in under 2 minutes,
   as part of a bigger grid, before smoothing existed). Fixed with a 1e-4
   floor on the smoothed state — inaudible, well below −80 dB.

3. **A persistent-state amplification burst — UNRESOLVED, marked unsafe.**
   `mask = |Wet|/|Dry|` is a division, and it is meaningless wherever `Dry`
   has negligible energy in a bin. Confirmed on real audio: `gtcrn` on the
   `male_lead` vocals-only probe, an otherwise *loud* frame (time-domain dry
   peak −3.6 dB), one bin at 20906 Hz read −124 dB below that same frame's
   own loudest bin while wet had residual energy there — mask clipped
   straight to the 8x ceiling on that bin alone. A per-bin reliability gate
   (bin energy vs. the frame's *own* peak bin, −60 dB threshold) fixed that
   specific case. A whole-frame (broadband) version of the same idea was
   tried *first* and does not work — a uniformly quiet frame has no internal
   contrast to fail that test against, so it let the exact burst through
   unchanged. But even after the per-bin fix, the **full real clip still
   showed a 3.48x peak / 2.55x RMS blowup** at a different point — a stale
   `smoothed` value from an earlier frame persisted through the release
   window and produced a burst dozens of frames later, in a frame that by
   itself showed zero anomalous bins. The bug is in how state *persists*
   across frames, not in any single frame's reading, and needs a different
   fix than frame-local gating. **Marked "NOT SAFE TO SWEEP YET" directly in
   the code docstring.** Do not point `--sweep mask_release_ms` or
   `mask_attack_ms` at real audio until this is root-caused.

**`floor_db` alone has none of this risk.** It is stateless — no frame can
affect another — already verified exact on synthetic cases (a fully-killed
band correctly restored from dry: 0.248 measured vs 0.251 expected), and now
confirmed stable on the *same* real clip that broke smoothing: peak/RMS
essentially unchanged at `floor_db=12/24` vs identity (0.4542 → 0.4540 /
0.4509 — no runaway, nothing like the smoothing blowup). A by-ear comparison
(`gtcrn`+`dtln` × `floor_db`=0/24/12, same 4-clip subset used throughout —
2 from `male_lead`, 2 from `nasheed`) has been rendered in full — 72 files,
4 clips × 2 ratios × 9 variants, with all three `floor_db` settings confirmed
distinct from each other on every clip/model/ratio pair, and no peak anomaly
anywhere (max 0.587). It is **awaiting the listen, not yet reported back.**
It is no longer the immediate next step — see the scope correction at the
head of this section, and "What to do next" near the top of this doc.

### State of the code

`tests/bench_quality.py` is the only file touched, and it is **uncommitted**.
It contains: `shape_mask()` with the one-sided exponent (verified),
`floor_db` (verified, safe), `attack_ms`/`release_ms` (implemented, marked
unsafe, do not sweep), and the `DEFAULT_CFG`/`config_id` plumbing for all
four `mask_*` parameters. Nothing in `assassin_live/` changed. `dialog.md`
is still untracked and still undecided.

## The by-ear results (2026-08-20) — read this before the 2026-08-19 set

**Sighted, not blind**, and through the app rather than `scripts/ab_listen.sh`,
so expectation bias is in it. The blind re-rank is still owed.

| finding | what the numbers say |
|---|---|
| stereo rebuild no longer doubles | gate 1 closed |
| `dpdfnet_hr` cuts a lot of vocals | matches: `vocal_ret` −5.0 dB, worst of four; `vocal-loss` 36/104 |
| `dtln` also cuts some vocals **and lets musical notes through** | matches: −20.6 dB music left vs −46.1; the largest single spread in the table |
| `gtcrn` has noise artifacts *and* cuts vocals; clearly worst | matches SAR 3.59, `artifacts` 47/104, `pumping` 49/104 — **not** its `musNoise` of −20, which is the hollow-spectrum trap the README documents |
| `dpdfnet` rated third, "no noise elements" | **conflicts with the data** — it is the only net-harmful model (dSI-SDR −0.28) with the worst artifact profile of anything tested (SAR 0.27, `artifacts` 73/104). Treat this placing as unreliable |

**No default change is justified by this session.** Neither `dtln` nor
`dpdfnet_hr` dominates the other — `dtln` wins vocal retention and latency,
`dpdfnet_hr` wins music suppression, SAR and musical noise, so both sit on
the Pareto frontier and the harness correctly declines to rank them. What the
session actually establishes is that **both frontier models audibly cut
vocals**, which makes the binding constraint the model *class* rather than the
model choice. That is an argument for B4 (which attacks vocal damage on every
model at once) over more A/B.

### The "lip sync" reports were three different things

Reported as: audio behind video, worse the longer it runs, with the audio
stopping for seconds at a time. Chased in order, each ruled out by
measurement rather than argument:

1. **Not the resampler.** The three 16 kHz models pay a 48k→16k→48k
   round-trip that `dpdfnet_hr` skips, so it was the obvious suspect.
   Measured at **0.00 ms at every soxr quality** — `ResampleStream`
   compensates its own delay.
2. **Not model latency.** The complaint is identical on all four, and the
   spread between them is large (`gtcrn` ~25 ms, `dpdfnet_hr` ~70 ms). If a
   45 ms spread is inaudible while the fault is obvious, the dominant term is
   model-independent.
3. **Not starvation or stream death.** The panel during a bad moment read
   **xruns 0, fallbacks 17 of 36781 blocks**, worker 3.9 ms against a 20 ms
   budget. A three-second stall alone would log ~150 fallbacks.
4. **What the panel actually showed: `input −37.3 dB, output −87.4 dB`** on
   `dpdfnet` at **166 % mix**. Signal in, silence out — the model deleting
   everything, not a timing fault. Three things stacked: the only
   net-harmful model, a very quiet source (**B10** — the models are
   level-dependent and nothing trims the input), and a mix past 100 % so no
   dry signal remains underneath to cover what the model removes.

**So B10 is no longer a theory about measurement conditions — it is a
user-visible defect.** It should move up the queue accordingly.

**Still open:** whether anything genuinely drifts. `latency_ms` cannot answer
it — that number reads `_dry_out`, which only grows when wet is *starved*,
while a pipeline running *ahead* backs up in `_out` and reports nothing. Two
readouts were added for exactly this (**added latency**, **output backlog**,
the latter flagged `— SATURATED` at the 160 ms cap). If the backlog ever
reads saturated, drift is real; if it sits low while audio sounds late, it is
not.

**Since narrowed, 2026-08-21.** Defect 3 in the next section is a mechanism
for permanent added delay that none of the four hypotheses above covers and
that these readouts would not have separated from real drift: switching
model mid-session baked in the ~140 ms cold start. If the sessions that
sounded late were sessions where the model was changed, that is the answer
and there is no drift to find. Read the backlog on a session with **no**
model switch to keep the two apart.

### Two defects found while chasing it (2026-08-20)

**1. The app crashes on device loss — fixed 2026-08-20.**
`sounddevice.PortAudioError: Invalid stream pointer [PaErrorCode -9988]`,
raised from `stream_ok`. `stop()` called `self._stream.stop()` and `close()`
unguarded, so when the device was already gone — the one case a teardown has
to survive — it raised, and `self._stream = None` never ran. The dead handle
stayed referenced; the 1 Hz supervisor polled `stream_ok`; PortAudio answered
"invalid pointer" instead of `False`; that exception landed in the Tk
callback and took the app down. `stream_ok` can no longer raise, and `stop()`
clears the reference in a `finally`. Verified against a simulated dead
stream.

**2. `tests/test_stereo_rebuild.py` is FLAKY.** It failed three separate runs
with *different* assertions each time (`set_stereo(True) restores the
surviving image`, `engine default is still the measured-mono behaviour`, the
latency-floor assertions) and passed others, with no code change between
them — confirmed by stashing. **"Green on the offline suite" has therefore
been unreliable**, and this is the test guarding the stereo rebuild and the
19.9 ms hand-off floor — the assertions much of the reasoning above leans on.
Worth fixing before it is trusted again.

### Three more defects, from two user hypotheses — fixed 2026-08-21

The user proposed two mechanisms the 2026-08-20 chase had not considered:
switching models while the filter is on, and something that echoes and
grows louder when the filter is switched off. Both were right, and reading
the switch path found a third. All three are fixed on this branch, with
`tests/test_live_model_swap.py` covering them (30 checks, hardware-free,
in `scripts/run_tests.sh`). **None of the three was reproduced first** —
they were found by reading, then demonstrated against the unfixed code.

**3. A live model switch reset none of the state a model change invalidates.**
`set_processor()` was three lines against the fifteen pieces of state
`start()` resets before a model is first allowed to see audio. The new
model inherited:

  * **the old model's measured lag** (`_mask_lag`) — and `_collect_lag()`
    only measures `if self._mask_lag is None`, so it was *never*
    re-measured. The stereo rebuild then shaped one model's output with
    another's mask.
  * **the old model's alignment padding** (`_mix_pad`), still sitting in
    `_dry_out`. The shipped spread is `gtcrn` ~25 ms to `dpdfnet_hr`
    ~70 ms, so a switch left the dry/wet blend up to ~45 ms apart — which
    is the echo that padding exists to remove (B8).
  * **no `reset()` and no `_warm_up()`**. `start()` warms specifically
    because the first inference after load takes 26 blocks (~0.5 s) for
    `dpdfnet_hr`, and audio arriving meanwhile piles into the dry FIFO,
    whose depth then IS the session's added latency: "cold start was
    baking in 140 ms of permanent delay instead of 20 ms". **A live switch
    reproduced exactly that, permanently, with nothing to work it back
    off.** This is the best match yet for "out of sync, worse the longer it
    runs" in a session where the model was changed.
  * the old model's queued input and processed output, spliced on.

Fixed by handing the swap to the worker thread (`_swap_runtime`), which
owns nearly all of that state — doing it from the UI thread was a race,
and warming there would have run the new model's first inference
concurrently with the worker's next `feed()` on the same object. The
callback clears its own FIFO and pad on a flag, since `_dry_out` is
callback-thread-only. A lag measurement in flight across a swap is
discarded by generation counter rather than applied to the wrong model.

**4. Turning the filter off could leave a feedback loop running with
nothing able to stop it.** `_tick()` had no `_busy` guard, and `_work_off()`
runs off the Tk thread so it cannot clear `self.enabled`. Sequence:

  1. `engine.stop()` sets `_stream = None` → `stream_ok` is False, but
     `enabled` is still True and `routing.real` still set.
  2. A 1 Hz tick lands in that window, takes the "stream died" branch and
     calls `engine.retarget()` — **opening a fresh live stream**.
  3. `_work_off()` continues into `routing.disable()` → `destroy_node(trap)`
     — **the trap sink destroyed under a running instance**, which is the
     2026-08-18 incident verbatim (`backends/pipewire.py diagnose_capture`:
     WirePlumber re-attaches the orphaned capture to the default sink's
     monitor, which is the sink we play into).
  4. `_poll_transition` does `self.engine = None`, dropping the only
     reference to a running engine. Nothing can stop it.
  5. `_tick`'s `if self.enabled and self.engine:` is now False, so
     `_check_capture()` — the feedback detector — never runs again.

The window is the worker join plus `list_sinks`/`set_default`/`destroy_node`
subprocess calls, roughly 0.2–0.8 s against a 1 s tick, so **this is not a
narrow race**. Fixed with a `_busy` guard; the recovery branch also no
longer retargets when `routing.real` is None, since recovering into an
unknown sink is another way to end up following the default.

**5. Five of `_tick()`'s exits never rescheduled the heartbeat.** It is
armed once, in `__init__`, so the first silent stream death, retarget
failure, `real_sink_lost`, `trap_lost` or detected feedback loop ended
supervision for the life of the process — including across a later
switch-on. Worst of the five was the `_check_capture()` exit: **the first
feedback loop turned the app off and disabled the only thing that can catch
the second.** `_tick` is now a wrapper that reschedules in a `finally`
around `_tick_body`, so no exit — or exception — can skip it.

### The residual latency was a ratchet — diagnosed 2026-08-21, NOT fixed

Reported after the switch-path fixes landed: the continuous drift was gone,
but the standing lag was still higher than early builds. The panel said why.
`added latency 107 ms` against `processor lag 29.3 ms` on `dtln` — **~78 ms
of it was not the model** — with `output backlog 37 ms` (nowhere near the
160 ms cap, so no drift), `xruns 0`, `worker 2.2 ms/block`, `fallbacks 36`
over `blocks 70441` (~23 minutes).

**`_dry_out` could only ever get deeper.** It is fed `frames` every callback
and drained by `take_dry = min(take, len(_dry_out))` where
`take = min(frames, len(_out))`, so the drain can never exceed the feed.
Only `stop()`, a model swap and the 160 ms cap ever shortened it. Every
block the worker failed to fill — 36 of them here — was delay kept for the
rest of the session, and a session therefore converged on its own
high-water mark. Driving the real `_callback_body` shows it plainly: three
starved blocks cost 60 ms, and a thousand healthy blocks afterwards, with
the worker at double speed and 140 ms of wet backlog piled up, gave back
**none** of it.

That also explains "bigger than in the first version" without a regression:
same build, longer session, higher water mark. It predicts that toggling
off and on drops the figure immediately, since `stop()` clears the FIFO.

#### The attempted fix made it much worse — reverted the same day

`_shed_slack()` dropped the same count from the head of `_dry_out` and
`_out` each block (0.5 ms, crossfaded), on the reasoning that their heads
refer to the same instant so equal drops preserve alignment. Shipped in
38db379, reverted in ef22d80 within the hour: **"much worse now, with some
pulsating beat in all models and latency etc."**

A periodic artifact on every model is the mix alternating between wet and
dry — `fallback_blocks`, at the ~50 Hz block rate — and the shed drove it
there. It took from `_out` as well as `_dry_out`, and its floor for `_out`
was **one block**. One block of wet backlog is not a margin; it is exactly
the depth at which any jitter in worker scheduling leaves `take < frames`
and the callback emits dry for that block.

**The reasoning error is the part worth keeping.** The commit claimed the
mechanism was self-limiting: shed too far, wet starves, shedding stops.
It does not, because shedding takes from *both* sides equally and so never
creates the starvation that would stop it. It parks the pipeline on the
edge of underrunning and holds it there.

**The tests could not see it, and that is also worth keeping.** The rig
used a zero-lag passthrough, whose dry FIFO drains to nothing unaided, so
shedding halted for a reason unrelated to any real pipeline. Re-run with a
processor carrying a realistic 29 ms lag and ±1.2 ms of worker jitter:

| configuration | fallbacks / 3000 blocks | wet left |
|---|---|---|
| no shed, no jitter | 0 | 1920 |
| no shed, with jitter | 21 | 65 |
| **shipped shed, with jitter** | **76** | **0** |
| shed with a 4-block wet floor | 21 | 65 |

3.6x the dry/wet alternation, and wet drained to nothing. **Any future
attempt needs a wet floor of several blocks** — but note the last row: at a
floor that is actually safe, it sheds nothing, because the slack and the
jitter margin are the same buffer. That may mean the ratchet cannot be
worked off from the callback at all, and belongs in `stop()`/restart or in
a resampler that changes rate by fractions of a percent instead of cutting
samples.

**The ratchet itself is unchanged and still real** — the same run shows a
dry FIFO climbing 1400 -> 1911 (29 -> 39 ms) on jitter alone, with no shed
anywhere. Only this answer to it was wrong.

### One finding deferred, then acted on 2026-08-21

`sd.Stream` was constructed with **no `latency=` argument**, so sounddevice's
default of `'high'` applied. Measured on the reporter's hardware: **34.8 ms
in + 34.8 ms out**, against 8.7 + 8.7 at `'low'` — roughly 70 ms of device
buffering that C7's number excludes *by design*, so nothing had ever
reported it. Switching to `'low'` was considered and rejected on 2026-08-20:
lower buffering means less tolerance for scheduling jitter, and with the
stream-death question still open it could make things worse.

**It was switched to `'low'` on 2026-08-21** on a screenshot of a 23-minute
session — `xruns 0`, `worker 2.2 ms/block` against a 20 ms budget — and
**reverted the same day** (ef22d80), alongside the shed it shipped with.

`xruns 0` was the wrong evidence. It counts what PortAudio notices, and the
failure that matters here is one it never sees: the *worker* arriving late,
which costs a wet block and a dry fallback with the stream perfectly
healthy throughout. Device buffering absorbs exactly that jitter, so cutting
it from ~70 ms to ~17 ms makes underruns likelier — the same direction as
the shed, at the same time, which is also why neither could be judged on
its own.

**If it is revisited, revisit it alone**, and judge it on `fallbacks` rather
than `xruns`. `details ▸` still shows **device buffer** (34.8 + 34.8 at the
restored default), read back off the open stream via `device_latency_ms`
rather than assumed from the request, since asking for a latency is only a
hint. That readout is all that survives, and it is the thing that was
missing: ~70 ms of audible delay that appeared in no number at all.

## The by-ear results so far (2026-08-19)

| finding | status |
|---|---|
| `dtln` leaves audible music noise and sounds muffled | matches the numbers: −20.6 dB music left vs `dpdfnet_hr`'s −46.1, and an air band at −59.7 dB (it is 16 kHz-native) |
| `dpdfnet_hr` drops vocals more, but leaves no music noise | matches: `vocal-loss` 36/104 pairs, `music_supp` −46.1 dB |
| stereo rebuild doubled the audio | **B6 fixed** — confirmed gone on `dtln`, and on `dpdfnet_hr` too as of 2026-08-20 |
| starting from idle came up late / out of sync | **B7, fixed** |
| the ON/OFF button gave no feedback and re-clicking reversed it | **C9, fixed** |

**`dpdfnet_hr` stays the default.** `dtln` and `gtcrn` are both 16 kHz-native
and delete everything above 8 kHz, which is disqualifying for anyone who
notices it — so the choice was never really three-way.

**Superseded 2026-08-20**, which found all four models cutting vocals and
neither frontier model dominating — see the section above.

**Treat every model ranking as provisional.** After B6 was fixed the same
listener reported `dtln` with "better audio retention and much less music
noise", which is a different judgement from the first pass. If the stereo
toggle was on during that first comparison, it ran through a mask 50 ms out
of step and every model would have sounded smeared and noisier. The
comparison is worth redoing now that the rebuild is correct — blind, via
`scripts/ab_listen.sh`, rather than through the app.

**The standing product judgement, which no metric captures:** vocal
preservation matters more than music removal. B1 ranked by dSI-SDR, which
weights them the other way, and that is how `dtln` came out ahead on paper
while destroying the top octave of every voice.

## The stereo work (B3) — built, measured, still shipped OFF

`assassin_live/audio/stereo.py` rebuilds the image by recovering the chain's
implied spectral mask from the audio (`|Wet| / |Dry_mono|` per bin) and
applying that one mask to both original channels. No processor changes, so all
four models get it at once. Full B3 sweep, whole corpus, 104 item-ratio pairs:

| config | music_supp | vocal_ret | dSI-SDR | stereo_width |
|---|---|---|---|---|
| `dpdfnet_hr` | −46.13 | −4.96 | **+4.19** | −160.93 |
| `dpdfnet_hr+st` | −46.13 | −4.96 | **+4.19** | **−6.14** |
| `dpdfnet_hr+ms4` | −50.52 | −7.41 | +3.23 | −160.93 |
| `dpdfnet_hr+ms4+st` | −50.52 | −7.41 | +3.23 | −8.17 |

Per category, stereo off → on: `sparse_acoustic` −161.5 → **−1.99 dB**,
`male_lead` −163.0 → −10.6 dB. The `stereo-collapse` failure tag goes
104/104 → 4/104. RTF +0.0065, latency +5.3 ms. Orthogonal to mid/side, which
keeps its own B1 penalty either way.

**Read the identical columns correctly.** Every mono metric is unchanged *by
construction*, not by luck: one mask applied to both channels leaves the mono
downmix arithmetically the same signal. The music that returns lives only in
the side channel, and **nothing in that table measures the side channel**
except `stereo_width_db`, which counts its energy without caring whether it is
voice or a guitar. So the sweep says "free as far as the harness can see", and
the harness is structurally blind to the one cost this change could have.
Hence gate 3 above. Off by default until someone listens; the
"Preserve Stereo Image" toggle is in the UI and persisted.

## Two retractions and one hole — read before trusting older claims

**`pipewire.sec.pid` is not the application's pid.** Anything arriving through
pipewire-pulse (how PortAudio's `pulse` device connects) is proxied, so the
client reports the *daemon's* pid — on this machine Firefox, GNOME's volume
control and our own streams all reported 2122. `pin_stream()` had therefore
been finding nothing and returning False **since it was written**, and C8's
capture detector inherited the same lookup and was silently vacuous — it would
never have fired on the incident it was written for. Use `our_stream_nodes()` /
`_owner_pid()` (`application.process.id`, falling back to `pipewire.sec.pid`
for native clients). Found only by running the C1 spike.

**A sink's volume does not scale its monitor.** C2 was promoted from a UX wart
to a *measurement confound* on the belief that the volume slider was changing
what the model is fed. Measured: a tone captured from a monitor is identical at
sink volume 1.0 and 0.5, **ratio 1.000**. The keys were inert, not corrupting.
The mirror still shipped (they work now), but —

**E2's `noise_only` swing of −55.7 → −21.7 dB across two identical hardware
runs has lost its only explanation and is now unaccounted for.** 34 dB of
run-to-run variance with no candidate. Do not treat that tier's absolute
numbers as reproducible.

## A1 (Phase 3) — spiked, and the framing was wrong

A1 is written around "RTF 0.067, ~15× headroom". Real, but measured on a 60 s
buffer. Against chunk size (1 thread, `scripts/spike_a1_separator.py`):

| chunk | ms/call | RTF | headroom |
|---|---|---|---|
| 0.25 s | 153.3 | 0.613 | 1.6× |
| 1.00 s | 129.0 | 0.129 | 7.8× |
| 8.00 s | 205.1 | 0.026 | 39.0× |

**Cost per call is flat.** It is fixed overhead, not work proportional to the
audio, so RTF improves only because the denominator grows. Latency is the
binding constraint and RTF is nearly irrelevant. Practical floor ≈ 1 s chunks
→ **≥1.1 s end-to-end**, so this can only ever be an additional high-latency
mode; the ~50 ms path is unreachable with it. That answers **Q3 by force**.

Also: peak RSS 478 MB; stems[0] is vocals, [1] accompaniment, confirmed by
cross-correlation.

## A1 — the processor exists now, and it corrected three of the spike's notes

`assassin_live/processors/spleeter.py` is the first `wants_stereo` processor:
chunked, overlap-discard, `spleeter_<n>ms` registry variants so the chunk is a
`--sweep` axis. `tests/test_spleeter_chunking.py` pins the behaviour;
`scripts/run_a1_sweep.sh` prices it. Building it moved three of the spike's
notes, all by measurement:

- **There is no remainder to carry.** Output length is the input truncated to
  a multiple of 1024 — *when fed at 44100*. The spike saw 44100 → 44032 and
  read it as loss; it is quantisation. Feed any other rate and sherpa
  resamples internally, the returned length is in the **output** rate, and
  input offsets stop matching output offsets. Hence `sample_rate = 44100`.
- **The impulse is at both ends, and it is far bigger than recorded.** Not
  ~26 samples at |466| at the head: on real corpus audio, **11043** in the
  first 8 samples against an interior peak of **1.28**, plus a smaller one
  (863) at the tail. Context is discarded on both sides. Emitted peak is now
  0.57× the input peak, no sample above 2.0.
- **Chunking is not an approximation of one-shot, and context does not fix
  it.** Interior chunks land **8–11 dB** below a whole-buffer run, flat across
  0.25/0.5 s of context and 1/2/4 s chunks. Not a seam artifact: one fitted
  gain per chunk explains almost none of it (−7.8 → −8.1 dB) and the residual
  is spread through the chunk, not piled at its edges. Spleeter masks a
  spectrogram whose context is the whole buffer, so a chunked run is a
  different computation. Whether that costs anything *audible* is what the
  sweep asks.

**The bench measured a chunked processor's latency as zero**, and its >120 ms
lip-sync failure tag could therefore never fire for the one processor it was
written for. A chunk-buffering processor emits output whose first sample still
corresponds to input sample 0 — the delay is in when samples become
*available*, and concatenating a file offline erases exactly that. `evaluate()`
now reports `max(measured, declared)`. This is the same blindness B3 has with
the side channel, in a different column.

**Do not read RTF from the A1 sweep as headroom.** It averages a 20 ms block
budget over a process that only runs every `chunk_ms`. The spike's table is
the latency authority.

### A1 sweep — complete, all three steps, and the curve never flattens

`~/.local/state/music-assassin/bench/a1`. Whole corpus, 104 item-ratio pairs:

| config | music | vocal | dSI-SDR | musNoise | stereo | lat ms |
|---|---|---|---|---|---|---|
| `spleeter_4000ms` | −35.03 | **−0.17** | **9.72** | 8.91 | −11.63 | 4093 |
| `spleeter_2000ms` | −36.83 | −0.22 | 7.89 | 15.84 | −12.72 | 2093 |
| `spleeter_1000ms` | −37.79 | −0.32 | 6.58 | 23.17 | −12.97 | 1093 |
| `spleeter_500ms` | −38.06 | −0.63 | 4.56 | 26.57 | −14.53 | 593 |
| `dpdfnet_hr` | **−46.13** | −4.96 | 4.19 | **1.02** | −160.93 | 50 |
| `spleeter_250ms` | −35.39 | −1.32 | 2.11 | 13.62 | −15.55 | 343 |

**The `dpdfnet_hr` row reproduces B3's whole-corpus row exactly** (−46.13 /
−4.96 / +4.19 / −160.93). That is the cross-check that the `wants_stereo`
branch added to `run_chain` left the mono path untouched — the baseline is
bit-for-bit the number B3 measured before the change.

Whole-corpus per-band vocal damage, sub/low/mid/high/air: `dpdfnet_hr`
−4.8 / −4.3 / −7.0 / −8.6 / −9.1 against `spleeter_4000ms` −0.3 / −0.1 /
−0.2 / −0.6 / −2.6. `dpdfnet_hr` fires `vocal-loss` on 36/104,
`hf-loss-4k` 30/104, `hf-loss` 28/104 and `stereo-collapse` 104/104; Spleeter
fires none of the first three at any chunk (bar 2/104 at 250 ms). It also
removes **109%** of the offline ceiling's music — it over-suppresses — while
reaching 17% of its dSI-SDR against `spleeter_4000ms`'s 40%.

`male_lead` (8 clips × 2 ratios), the category where B1 found `dpdfnet_hr`
weakest and most vocally damaging, is the same story amplified:

| config | music | vocal | dSI-SDR | musNoise | stereo | lat ms |
|---|---|---|---|---|---|---|
| `spleeter_4000ms` | −38.85 | **−0.40** | **8.65** | 21.29 | −12.86 | 4093 |
| `spleeter_2000ms` | −41.04 | −0.46 | 6.65 | 33.58 | −13.95 | 2093 |
| `spleeter_1000ms` | −40.51 | −0.53 | 4.77 | 38.98 | −14.75 | 1093 |
| `spleeter_500ms` | −41.81 | −0.77 | 2.38 | 47.97 | −17.04 | 593 |
| `dpdfnet_hr` | **−48.38** | −8.47 | 0.33 | **6.81** | −162.98 | 50 |
| `spleeter_250ms` | −35.54 | −1.43 | 0.13 | 30.72 | −18.56 | 343 |

**dSI-SDR rises monotonically with chunk size and is still rising at 4 s**, on
both categories swept. The sweep was written to find where it flattens so the
smallest acceptable chunk could be read off; in this range there is no such
point. Quality keeps buying latency for as long as you will pay, which
sharpens A1 rather than settling it — the configs that beat the shipped
default are the least shippable ones.

**Per-band is where the case actually is.** On `male_lead`,
sub/low/mid/high/air vocal damage: `dpdfnet_hr` −8.1 / −7.3 / **−11.3** /
**−11.7** / **−13.0** against `spleeter_4000ms` −0.7 / −0.3 / −0.4 / −1.6 /
−2.8. `dpdfnet_hr` fires `vocal-loss` 8/16, `hf-loss` 4/16, `hf-loss-4k`
2/16; no Spleeter config fires any of them. It reaches **1%** of the offline
ceiling's dSI-SDR here, against 38% for `spleeter_4000ms`. This is not the
SI-SDR scoring trap — the band table measures the damage directly.

**What Spleeter pays for it:** 7–10 dB less music removed (−39 vs −48) and
3–7× the musical noise (tag fires 8–15/16 vs 5/16). `long-burst` and
`pumping` also fire more. So the trade is the mirror image of the shipped
default's: voice intact, more music left, noisier.

**The chunking penalty scales with chunk size, as the −8 to −11 dB
chunked-vs-one-shot gap predicted.** `boundary-sensitive` over the whole
corpus: 102/104 at 250 ms, 70 at 500 ms, 28 at 1 s, 14 at 2 s, 4 at 4 s.
Musical noise tracks it (26.6 → 8.9 across the same range), so the artifact
cost and the quality gain point the same way — both argue for bigger chunks,
which is the whole problem. 250 ms is the only config whose vocal damage is
worse than `dpdfnet_hr`'s in any band.

The `chunk_all` step took 7.5 h; the two scoped steps 34 and 72 min.

On `sparse_acoustic` the same shape holds (dSI-SDR 4.27 → 13.06 across the
range, `dpdfnet_hr` 9.39) but the gap is far smaller, because `dpdfnet_hr`
is not damaging voices there — vocal −0.88 rather than −8.47.

**Still needs ears.** Audio is dumped at `a1/audio_sparse` and `a1/audio_male`.
The open question is the product one: is "voice intact, more music left,
noisier" better than "music gone, voice chewed"? No metric here settles it.

Environment: sherpa-onnx stays **out** of `requirements.txt` (imported lazily,
so the app venv is unchanged and the separator simply does not appear there).
Use `~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python` (1.13.4). Models
come from `Music-Assassin/models/sherpa_onnx/sherpa-onnx-spleeter-2stems-int8/`
(2 × 26 MB — note the `sherpa_onnx/` path component); `scripts/import_models.py`
now copies them in under the names the registry looks up.

## What landed 2026-10-27 — "keep sound effects" live, and what it costs

Asked for a switch to choose whether to preserve sound effects. There was no
mechanism for one to drive: the app had no effects path at all, and the two
grep hits for "effects" in `assassin_live/` were comments naming Easy
Effects as a competitor. The options were put to the user, who chose the
full MRX path over the three cheaper stand-ins.

**What shipped.** MRX (MERL cocktail-fork, int8 ONNX, MIT, 31 MB) as
`processors/mrx.py`, a torch-free chunk STFT as `processors/cstft.py`,
`scripts/fetch_mrx.py`, `models/mrx.json`, `AudioEngine.set_keep_effects`,
and a switch on the Processing card that greys out with a reason for
pipelines that have no effects stem. `Switch` grew a disabled state.

**Why a model rather than a knob.** This is the same wall as B4. Every other
processor emits one scalar per bin saying how speech-like it is; drums and
effects are adjacent in that one ranking, so no monotonic function of it
separates them. A separate effects mask is a second axis. ROADMAP §11's
2026-10-23 scope note already said the dead end was *pure-DSP* add-back and
not add-back as such.

**Three things measured, and only the first is comfortable.**

- **Cost is fine.** RTF ~0.10 on one thread; 83 ms of CPU per second of
  audio, flat from 1 s to 8 s chunks. One pass yields both stems, so MRX
  replaces `dpdfnet_hr` rather than running beside it. An early reading of
  0.33 was onnxruntime shape-planning amortised over 6 s of audio, not
  steady state — worth knowing before anyone re-measures and panics.

- **The chunk is both the latency and the quality.** Error against a full
  30 s pass falls about 3 dB per doubling, and the effects stem trails the
  dialogue stem by 3–5 dB at every chunk. **Nothing inside a live latency
  budget reproduces the 30 s result** the offline app ships. At 500 and
  1000 ms the effects stem's error exceeds its own energy. The default is
  2000 ms — 1805 ms of latency — as the shortest chunk where it does not.
  This retires the "≥ 1.1 s" figure we had been quoting from the Spleeter
  spike; it was never MRX's number.

- **The real limit is the training domain, not the plumbing.** MRX is
  trained on DnR: film speech, score, foley. On 4 b3 male-lead clips, which
  contain **no sound effects at all**, so every bit of the effects stem is
  leakage: −6.2 dB relative to dialogue raw, −10.6 dB after
  over-subtraction, and −0.7 dB on the worst clip. Offline on cinematic
  content the same model measures −21.3 dB. **On for film and TV, off for
  music with singing.** No amount of wrapper work changes this.

**One parity gap found and closed.** The offline path applies
`clean_effects()` over-subtraction by default for MRX and the first cut here
did not. Doing it in the model's own three STFT domains instead of a
separate 2048/512 analysis costs two multiplies per bin and no extra
transform, and gives −4.4 dB against the offline implementation's −4.9 dB,
which cross-validates the shorter route.

**What was verified rather than assumed.**
- `cstft` against torch itself, 9 length/window combinations, ~2e-7
  relative. MRX's weights are int8, so `normalized=True`'s `1/sqrt(n_fft)`
  is not a detail: getting it wrong feeds the quantised graph magnitudes it
  was never calibrated for, and a round-trip test would not notice.
- Count alignment by searching offsets either side of 0 and insisting 0
  wins: it does, by 42 dB. The engine's positional dry/wet pairing depends
  on this, and a one-hop slip is an echo at anything under 100 % mix.
- `Switch.set_enabled` under `xvfb-run` — ignores clicks, keeps its value,
  draws in all four enabled/value combinations. NOT added to
  `scripts/run_tests.sh`, which is display-free by design.

**Still open.** Every number above is from music clips, which is MRX's worst
case; it wants a listen on real cinematic content. Whether 1805 ms is
acceptable is the latency-reporting question (CURRENT_STATE item 4), still
untested. Not bundled in the .deb: DnR's component-corpus terms are
unchecked, the same question as StemgenRT's.

## What landed 2026-10-22

All uncommitted on `feat/separator-spike`. This is the three-step plan from
`docs/RESEARCH_PRIOR_ART.md` §2.11.

**Engine — chunked processors and lockstep (§2.2, §2.3).**
- FIFO caps are sized from the installed processor
  (`BASE_FIFO_BLOCKS` + 2× its declared hold).
- A trim on either side drops the matching samples from the other:
  `_dry_drop` (worker → callback) and `_wet_skip` (callback-only), netted
  when both fire over one stall.
- The worker's per-block job is split out as `_process_block()`, so
  `tests/test_engine_chunked.py` can drive the real engine. Before the fix a
  1 s chunked processor came out 16 % processed; it is now 100 %.

**StemgenRT (A2).** `processors/stemgen.py`, `scripts/fetch_stemgen.py` and
`models/stemgen_rt_vocals.json` — details in ROADMAP A2. The model lives in
the data dir and, since 2026-10-24, is bundled in the .deb.

**Mask shaper (B4, C12).**
- `audio/maskshape.py` runs in the engine after the processor.
  `set_atten_limit` drives the shaper's floor for every model except one
  with a native setter (`speechdenoiser`). `set_smoothing` sets `release_ms`.
- The panel's "Suppression limit" row is always visible now, with a new
  "Smoothing" slider (0–150 ms, saved as `smoothing_ms`). Headless flags:
  `--limit-db` and `--smoothing-ms`.
- `tests/test_mask_shaper.py` and a toggle test in `test_engine_chunked.py`
  cover it, and both are in `run_tests.sh`.
- Both controls default to off; no default is chosen before a sweep and a
  listen.

**Bench.**
- `shape_mask()` now delegates to `MaskShaper`. Its inverted alignment is
  fixed; see the retraction at the head of "The permissiveness knob".
- One deliberate behaviour change: while shaping is on, a bin the model
  boosted above its dry is capped back to it.

**Sweep logs must not go to the sftp mount.** Two sweeps died with exit 1
and an empty log. The first stdout write after ~30 minutes of silence (the
reference phase prints nothing) fails on the gvfs file, and the traceback
can't be written either. Point `>` at local disk; reads from the mount are
fine. Importing `bench_quality` over the mount alone takes ~2 minutes.

**Also (2026-10-23; retracted in `85328cf`, re-confirmed 2026-10-24 — see
RESEARCH_PRIOR_ART §2.8):** `audio/stereo.py`'s `MAX_GAIN` went from 8 to 1.
With anti-phase content plus a residual in wet, the 8× clamp amplified the
pair by +17 dB. The new test reproduces that case and failed (6.89×) before
the fix.

**Not done.**
- Defaults for floor and smoothing.
- The latency ratchet ("The residual latency was a ratchet").

## What landed 2026-08-21

Three defects behind the user's two hypotheses — live model switching, and
an echo that grows after switching off. Written up in full under "Three
more defects" above; in short:

- **`set_processor()` now resets what a model change invalidates.** The
  swap moved to the worker thread (`_swap_runtime`), which owns the state;
  the new model is warmed before it goes live, the old model's measured
  lag and alignment pad are dropped and re-measured, and its queued and
  processed audio is discarded rather than spliced. A lag measurement
  racing the swap is invalidated by generation counter.
- **The supervisor no longer acts during a transition** (`_busy` guard),
  which closes a 0.2–0.8 s window in which turning the filter off could
  open a fresh stream and then have the trap sink destroyed underneath it
  — the 2026-08-18 feedback loop, with the engine reference dropped
  immediately after so nothing could stop it.
- **The 1 Hz heartbeat can no longer stop itself.** Five exits returned
  without rescheduling and it is armed only once; the worst was the
  feedback detector's own exit. `_tick` now reschedules in a `finally`.
- **`tests/test_live_model_swap.py`** — 30 checks, no Tk/audio/PipeWire,
  registered in `scripts/run_tests.sh`. Each defect was demonstrated
  against the unfixed code before the fix went in.

Not reproduced by ear yet: all three were found by reading the switch path.
The by-ear confirmation is folded into next-step 3.

Then a second attempt, at the standing latency, which **failed and was
reverted** (38db379, reverted by ef22d80). `_shed_slack()` plus
`STREAM_LATENCY = "low"` produced "much worse now, with some pulsating beat
in all models" and went back within the hour. Both had the same effect from
opposite ends — less margin against worker jitter — and the audible result
is dry/wet alternation at the block rate. Full post-mortem, including the
reasoning error and why the tests missed it, under "The residual latency was
a ratchet" above.

What survived that round, both display-only and neither in the audio path:

- **`device buffer` in `details ▸`** — PortAudio's own in/out buffering,
  read off the open stream. ~70 ms at the restored `'high'` default, and it
  had never appeared in any readout, because `latency_ms` excludes it by
  design.
- **The details label column, 14 -> 16.** `output backlog` is exactly 14
  characters and was rendering as `output backlog37 ms`. Guarded in
  `test_status_line.py` against every key the block uses.

**The ratchet is still open and still unfixed.** It is the reason the
standing lag grows with session length.

## What landed 2026-08-20 (third session)

- **Both feature branches pushed**, CI exercised for the first time and green
  on both (see "Where the code is" for what green does and does not cover).
- **README gained a "Measuring quality" section** — every quantity the
  harness reports, in plain terms, plus the four reference points, the
  failure tags, and the whole-corpus results for all four benchmarked models
  (headline numbers, per-band vocal damage, per-model tag counts), pulled
  from the B1 run's CSVs rather than restated from the roadmap. Two
  corrections came out of checking those numbers: per-band damage on
  `dpdfnet_hr` is **nearly** double bottom-to-top, not "more than double" as
  ROADMAP B9 still says (−4.8 → −9.1 dB whole-corpus, −8.1 → −13.0 on
  `male_lead`), and `dtln`'s vocal advantage over the next best is ~2.6×, not
  3×. It also records that **`speechdenoiser` has never been benchmarked** —
  it is in the model table but in no sweep.
- **`scripts/build_deb.sh` made portable** (ffmpeg fallback for icons,
  `python -m PyInstaller`, overridable `BUILD_DIR`), and **0.1.4 built and
  verified** — package well-formed, four redistributable models bundled,
  `speechdenoiser` excluded, eight icon sizes, binary answers `--help`. It is
  in `dist/`, unreleased.
- **Crash on device loss fixed** — `stream_ok` can no longer raise and
  `stop()` always clears the stream reference. See the by-ear section.
- **Two readouts added to `details ▸`**: `added latency` (the engine's own
  measured figure, previously only on the status bar) and `output backlog`,
  which is the `_out` depth — the blind spot `latency_ms` structurally cannot
  see, flagged `— SATURATED` at the 160 ms cap.

## What landed 2026-08-19/20 (UI)

- **C11** — the control panel is a 680 × 430 two-column window: capsule
  switches instead of three full-width buttons, a pill power control, meters
  on a dB scale, hold-to-compare, and a status bar welded to the bottom
  edge. Colour means one thing at a time now — red is the brand and the
  quantity being changed, green is *running*, red text is a fault and only
  ever text. Researched against SoundSource 6, Easy Effects, NVIDIA
  Broadcast and Krisp first; the five things the drawing got wrong and a
  render corrected are in ROADMAP C11.
- **Antialiasing.** The Tk canvas has none, so every circle in the panel had
  a stepped edge that read as a smudge. Shapes are rendered from a signed
  distance field with numpy and blitted as PNGs (`widgets.aa_shape`). The
  icon badge loads a pre-rendered size instead of `subsample()`, which is
  point sampling.
- **The dropdown list is drawn**, not delegated. `tk.Menu` came up narrower
  than its field, offset from it, and took the *system* palette on the
  user's machine — the same failure `ttk.Combobox` had. `widgets._Popup` is
  a borderless toplevel that owns its pixels: field width, aligned, current
  item marked, keyboard and wheel, global grab, flips above when there is no
  room below.
- **Status messages hold** (4 s, 20 s for faults), because the tick used to
  erase them within a second.

## What landed 2026-08-19 (second session)

- **B8** — the dry/wet mix carried B6's skew. The lag pad reached
  `_dry_work` (the mask) and not `_dry_out` (the mix). Both now, handed to
  the callback thread the way `_pending_lag` is handed to the worker.
  `latency_ms` subtracts the pad and the FIFO's cap grows by it, or the
  correction would be trimmed away again. Regression test measures 0.0 ms
  skew, and 49.9 ms with the fix disabled.
- **B6b** — the lag estimator could give up and call it zero. Silence no
  longer consumes windows (20 s of it used to cost thirteen), and exhaustion
  now means *unmeasured* — no correction, rebuild bypassed, and a state the
  UI names — rather than a silent assumption of zero. Falling back to the
  processor's declared latency was considered and rejected: `dpdfnet_hr`
  declares 10 ms and measures 50, so that is a 40 ms error, not a fallback.
- **C10** — the meter draws input above output, and the details panel prints
  both as dBFS. Diagnostic for open item 2 above.
- **C11** — control panel rebuilt as the 680 × 430 two-column panel:
  capsule switches instead of three full-width buttons, a pill power
  control, `tk.Menu`-backed dropdowns instead of `ttk.Combobox`, the two
  meters on a dB scale, hold-to-compare, a wet-boost readout, and a status
  bar welded to the bottom edge. Colour now means one thing at a time — red
  is the brand and the quantity being changed, green is *running*, and red
  text is a fault and only ever text. Details in ROADMAP C11, including the
  five things the drawing did not settle and a render did.
- **Status messages now hold** (4 s, 20 s for faults). The tick used to
  overwrite the line every second, so a fault was erased before it could be
  read — from the one screen this handover tells you to go and read when the
  audio stops.

Suite green after all of it, including the model tier
(`test_processors_offline`).

## What landed 2026-08-19 (first session)

- **B6** — the stereo rebuild's mask was 50 ms out of step on `dpdfnet_hr`,
  because the engine paired wet with dry by FIFO position. The lag is now
  measured at stream start (`audio/lag.py`) and the dry stream delayed by it.
  Two mistakes on the way, both recorded in ROADMAP B6: correlating on the
  inference worker took the pipeline floor from 20 ms to 60 ms, and a
  tone-only test signal reported lag 0 with confidence 0.46 because the true
  lag was ~11 periods of the test tone.
- **B7** — cold ONNX warm-up took 26 blocks against 1 warm, and everything
  arriving meanwhile piled into the dry FIFO, whose depth *is* the session's
  latency. 140 ms baked in instead of 20, varying run to run. The processor
  is now warmed inside `start()` before the stream opens.
- **C9** — the ON/OFF button ran its work on the Tk thread, so it could not
  repaint and a second click *queued* rather than cancelling, undoing the
  action. Now threaded, with four states and disabled while transitioning.

## What else landed 2026-08-18

- **C1** live output retarget: a `target.object` write moves a running stream
  in 0.01–0.04 s with ~1.5 ms of callback jitter and no xruns (spiked 3×). The
  teardown path is gone for output changes, and the model keeps its state.
- **C3** the picker: debounce 1.5 s → 0.25 s (its stated reason — "each
  restart is an audible glitch" — died with C1), dropdown follows external
  changes, one code path for all three ways an output changes.
  `real_sink_changed` split from `real_sink_replaced` so a sleeping headset no
  longer overwrites a saved preference.
- **C4/C7** status line: `Filtering → device · latency · health`, counters
  behind a toggle. Latency is **measured** from the lockstep FIFO. Its floor
  is one block of queue hand-off (~20 ms) even with a zero-latency model —
  relevant to A1, whose budget starts 20 ms in debt.
- **C8** capture verification every ~5 s: `trap_lost`, `feedback_loop`,
  `capture_hijacked`. Turns off immediately on a loop rather than attempting a
  repair that would spend 3 s howling.
- **E1** CI: `scripts/run_tests.sh`, one entry point for humans and CI.
  `test_processors_offline.py` used to print "all passed" with no models
  installed — a vacuous green; it now says `NOTHING TESTED` and exits
  non-zero, and the runner reports it as SKIPPED.

## Open questions for the user

Full list in ROADMAP §10. Live ones:

- **Q3 latency ceiling** — effectively answered by the A1 spike: a separator
  costs ≥1.1 s. The remaining question is a product one: ship it as a
  high-latency mode, or not at all?
- **Q5 mono output** — the engineering half is settled (the fix cost one
  module). What is left: if the by-ear pass finds the restored side channel
  brings back audible music, is shipping mono *and saying so in the UI*
  acceptable?
- **Wide-stereo corpus** — ~6–10 hard-panned / EDM / out-of-phase / binaural
  clips. The corpus proves the rebuild works but cannot show it failing
  gracefully (median S/M −8.2 dB, nothing hard-panned).
  `bench_quality.py` already reserves the category name `stereo_torture`.
- **Answered 2026-08-20:** the stereo rebuild no longer doubles, and the
  "lip sync" family of reports resolved to total suppression on a
  net-harmful model rather than any timing fault (see the by-ear section).
- **New, and it needs one reading, not a discussion:** does `output backlog`
  in `details ▸` ever show `SATURATED`? That single word decides whether the
  pipeline genuinely drifts or whether every remaining sync complaint is the
  suppression defect wearing a disguise.
- **Q1** no longer blocks B4 — the tag counts are reason enough to build it
  and the sweep decides whether it ships. The answer still shapes what a
  listener should be checking for when candidates exist.
- **Q2** (blocks C5, resume-on-launch) unchanged.
- **New, and B4 will ask it:** how much *musical noise* is an acceptable
  price for keeping a syllable? `dpdfnet_hr` scores 1.02 there, the best of
  anything measured, and smoothing a mask is exactly the operation that
  trades that away. The sweep can price it; only a listener can accept it.

## Environment notes

- Run the suite with `./scripts/run_tests.sh` — seven hardware-free files
  (~10 s), plus the model benchmark (~80 s) when the ONNX files are present.
- **`tests/test_stereo_rebuild.py` is flaky** (found 2026-08-20) — it fails
  intermittently with different assertions each run and no code change
  between them. Until that is fixed, re-run the suite before believing a
  failure *or* a pass.
- **`tests/test_routing_dry.py` is not hardware-free** despite its name and is
  deliberately excluded from the runner. It destroys every sink named
  `MusicAssassin`; on 2026-08-18 it did that to a running instance, and
  PipeWire re-attached the orphaned capture stream to the real output's
  monitor — an audio feedback loop that ran until the app was killed. It now
  refuses when it finds a live instance (`ALLOW_LIVE=1` overrides).
  `tests/test_live_e2e.py`'s hardware tier also takes over the default sink;
  `python -m assassin_live --recover` restores it.
- Python: `.venv/bin/python` in the repo root. Models resolve from
  `~/.local/share/music-assassin/models`, the repo's `models/`, or
  `/usr/share/music-assassin-live/models` (the `.deb`'s).
- **`models_dir()` resolves somewhere surprising under the VS Code snap.** It
  honours `XDG_DATA_HOME`, and a terminal inside the snap exports
  `XDG_DATA_HOME=~/snap/code/<rev>/.local/share` — so `~/.local/share/
  music-assassin/models` is never consulted, resolution falls through to the
  `.deb`'s `/usr/share` subset, and a run dies with *"model(s) not installed"*
  naming models that are installed. Set `MUSIC_ASSASSIN_MODELS` explicitly for
  anything long-running; `run_a1_sweep.sh` warns when it is unset.
- Corpus and sweep outputs under `~/.local/state/music-assassin/bench/`
  (`corpus/`, `b1/`, `b3/`). **The corpus lives on the machine that built it**
  — `bench_quality.py --corpus` and the sweep scripts' `CORPUS=`/`OUT=` let a
  run read it over a mount, but RTF and ms/block are then properties of
  whichever machine ran it and are not comparable to B1's.
- Building the harness's reference corpus needs `demucs` (torch), which this
  app deliberately never depends on — run it out-of-process via
  `--demucs-python ~/Documents/venvs/assassin_venv_v0.4.4_cpu/bin/python`.
- **Detach long runs, and never edit a running script.** `setsid nohup … &`,
  verify SID == PID. bash reads scripts incrementally by byte offset, so
  editing one mid-run makes it resume at a shifted position — observed
  2026-08-18: the sweep jumped backwards and re-ran a completed step, silently
  skipping the next one.
- **Excerpt before separating, and use `htdemucs`.**
  `scripts/corpus_excerpt.py` cuts a 30 s excerpt, refuses acapella-looking
  sources, and biases its window toward the first vocal entrance rather than
  the loudest moment. `mdx_extra` peaks at 3.6 GB / 132 s; `htdemucs` is
  1.2 GB / 39 s and is the corpus's separator of record.
- `.claude/worktrees/` is scratch space for parallel agent worktrees; still
  not gitignored.
