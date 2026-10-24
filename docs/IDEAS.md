# Idea ledger — everything considered, built or not

This page exists for the moment when the obvious next step is not working and
you need to see the option space again. It lists **every idea that has been
raised** for this project: shipped, half-built, parked, never tried, and dead.
Being dead is not a reason to leave an idea out — *why* it died is often the
most useful thing on the page, because it prunes a whole family of its
relatives.

The other documents answer different questions and should not be duplicated
here:

| document | answers |
|---|---|
| `CURRENT_STATE.md` | what is true now |
| `ROADMAP.md` | what is planned, and why, in order |
| `ROADMAP.md` §11 | what is rejected, with the measurement |
| `RESEARCH_PRIOR_ART.md` | what exists outside this repo, and a pipeline review |
| `LIVE_SFX_HANDOVER.md` | the sound-effects problem specifically |
| `HANDOVER.md` | the session-by-session record and its corrections |

This page is the index across all of them, plus the ideas that never made it
into any of them. **Add to it freely** — an idea costs two lines here and the
cost of losing one is a repeated investigation. Started 2026-10-24, and it
absorbs the symptom-organised list that was drafted in parallel as
`RESEARCH_IDEAS.md`; if both still exist, this is the one to keep.

---

## 0. Status vocabulary

- **shipped** — in the app and reachable by a user
- **committed** — in the code, not the default, not yet swept
- **measured** — numbers exist; read them before re-deciding
- **parked** — deliberately waiting on something named
- **idea** — never built, never measured
- **blocked** — cannot proceed; the blocker is named
- **dead** — measured or reasoned out; do not redo without new information

---

## 1. Four principles that prune whole families of ideas

These are worth reading first. Each one kills several plausible ideas at
once, and each was learned the expensive way.

### 1.1 A monotonic reshape of one number adds no information

A speech enhancer emits one scalar per bin: how speech-like it is. An
exponent, a floor, a threshold — any monotonic function of that scalar —
preserves its *ordering*. So any setting that lets a wanted bin through also
lets through every bin the model ranked as more speech-like. If two things
you want to separate sit next to each other in that ranking, no amount of
reshaping will separate them, because the discriminating information was
discarded by the model before the knob ever ran.

This is why B4's mask shaping could never recover sound effects: drums and
effects are adjacent in that ranking, sharing the features the model keys on
(broadband, transient, inharmonic). Selectivity needs a **second, independent
axis of evidence** — a different model, a classifier, a different
decomposition, or a different physical quantity. Ask of any new idea: *what
independent axis does this add?* If the answer is "none", it is a volume
control.

### 1.2 A separator's taxonomy bounds what you can ask of it

With stems `V`, `D`, `B` from a mix `M`, the residual `R = M − V − D − B`
holds everything unmodelled. If two things you care about both live in `R`,
the only control you have is `V + α·R` — one scalar trading them against each
other at a fixed rate. That is not selectivity, it is the same trade as 1.1
from a better starting point. **The stem set decides what is expressible**, so
read a model's taxonomy before its benchmark scores.

### 1.3 You cannot measure what the ground truth defines away

`bench_quality.py` builds references by separating sources into **vocals and
music** and remixing at known ratios. Two classes. Anything that is neither —
effects, ambience, room tone — lands in the music stem by construction. So
every metric in the harness scores "an effect was preserved" as *music leak*,
i.e. a regression. A mechanism that did exactly what was wanted would have
come back looking worse.

Before optimising anything, check that the objective contains the goal. If it
does not, fixing the fixture comes first, and sweeping harder is wasted.

### 1.4 Clamping a quotient is not the same as fixing its denominator

When a ratio blows up because its denominator went near zero, capping the
result treats the symptom. The cap then also binds in the cases where a large
ratio was *correct*. Measured instance: the stereo rebuild's
`|Wet| / |Dry_mono|` with `MAX_GAIN` — 8.0 amplifies anti-phase content by
+17 dB, 1.0 collapses the stereo image entirely, and nothing in between does
both, because the formulation legitimately needs gain > 1 for content that
cancels in the mono sum. Fix the reference, not the quotient (§5).

---

## 2. Separation quality — getting music out, keeping voice

| idea | status | notes |
|---|---|---|
| Causal music separator instead of a speech enhancer | **committed** | StemgenRT as `stemgen`. RTF 0.094, 5.8 ms. `male_lead` sweep: +9.9 dB voice gain, 0 dB voice lost, image kept, vs `dpdfnet_hr`'s +0.3 / −8.5 / collapsed. Cost: −24 dB music left vs −48. ROADMAP A2 |
| Longer analysis window for StemgenRT | **idea** | It uses ~6 ms of a ~120 ms budget. The single most obvious quality lever left, and untried |
| Music-subtraction: `vocals = mix − music` | **parked** | Preserves effects because nothing classifies them. Needs a model that estimates *music* without catching effects; StemgenRT's drums+bass is not enough. ROADMAP A4 |
| Train our own causal separator | **idea** | Trainer now available (`StemgenRT-5.8`, MIT). Months, not weeks. `RESEARCH_PRIOR_ART.md` §1.2 |
| A slow separator as an online teacher | **idea** | Highest effort of the §1.6 levers. `RESEARCH_PRIOR_ART.md` §1.6 #7 |
| Distil a causal student from CDX23 | **idea** | `LIVE_SFX_HANDOVER.md` §4.4 |
| Retrain on permissive data | **idea** | Would also resolve the Q6 licence blocker, which no measurement can settle |
| Retrain `gtcrn`/`dpdfnet` with clean = dialogue + effects, noise = music | **idea** | Needs no app changes at all — same interface, different weights. The cheapest route to an effects-keeping model |
| Fine-tune `stemgen` on anime-opening vocals | **idea** | If its licence and data allow (Q6) |
| Small causal cleanup model on top of `stemgen` | **idea** | Predicts a correction mask rather than redoing separation |
| OpenVINO with BF16 | **idea** | 2–3× on one large model in the offline app. The live notes only tested OpenVINO *through sherpa*, which statically links its own runtime. Relevant only if a large model goes live |
| Demucs-class offline separators, live | **blocked on latency, not compute — worth re-pricing** | Their voice-keep-against-music-removal still beats everything tested live, which is the standing argument for revisiting them. They need **seconds** of future audio: Spleeter's quality keeps rising through 4 s windows and never flattens, and MRX/CDX23 are the same. So they can only run in a movie mode with a declared delay, which makes this a child of §6 rather than an independent idea. If a player does compensate, this becomes the strongest quality option on the page |
| Streaming an unmodified offline model | **dead** | A ceiling, not a product. `RESEARCH_PRIOR_ART.md` §1.2 |
| Any additional speech enhancer | **dead** | Category exhausted; all dominated by `dpdfnet_hr`. ROADMAP §11 |

## 3. Keeping sound effects

The problem: speech enhancers keep voice only, so footsteps and explosions
leave with the music. Offline this is **solved and preferred by ear**; live it
is not available from any existing model.

| idea | status | notes |
|---|---|---|
| Second non-causal model extracts an effects stem, add it back | **measured, offline only** | CDX23 −24.9 dB leak / +3.6 dB keep; MRX −16.4 / −0.3. Both need seconds of future audio. `LIVE_SFX_HANDOVER.md` §2 |
| Over-subtraction cleanup of the effects stem | **measured** | Per STFT bin, gain = `max(0.05, 1 − β × music/effects)` on magnitudes, using the model's own music estimate. Works for MRX (−16.4 → −21.3 dB at β=1). On by default for MRX offline |
| Music-presence gate, soft with hysteresis | **idea — cheapest live route** | `out = p·processed + (1−p)·dry`. EfficientAT mn04 or YAMNet ONNX. Keeps effects only where music is absent; ~0.3–0.5 s leaks at each onset. `LIVE_SFX_HANDOVER.md` §4.1 |
| Classifier gate on the residual `R = dry − wet` | **idea** | `R` is what the enhancer removed: music plus effects. Easier than classifying the full mix, since voice is already gone. ROADMAP A4's own unpark route. `LIVE_SFX_HANDOVER.md` §4.2 |
| Movie mode: non-causal effects path with declared latency | **blocked** | Needs 0.5–1.5 s and honest latency reporting (§6). Q3 and Q7 gate it |
| Browser look-ahead companion | **idea** | Processes ahead of the playhead, so no realtime constraint and offline models run unchanged. Nearest route that works *today* |
| Causal three-way dialogue/music/effects model | **blocked** | None public and causal. LiteCASS has no weights; DnR Demucs is offline |
| Semantic classifier (YAMNet) gating add-back | **idea** | ROADMAP §11 calls it "the only thing that works" at 0.5 % leakage. Now ONNX-convertible, so the old TF blocker is gone |
| Harmonic/percussive separation as the second axis | **dead (reasoned)** | Recorded here for the first time. HPSS would split tonal music from transients — but drums are percussive too, so it returns the single most objectionable leak. Same collision as 1.1 |
| Periodicity as the drum/effect discriminator | **idea** | Also new here. Drums are rhythmically periodic; effects are aperiodic one-shots. A beat-tracker or periodicity estimate is an axis *neither* the mask value nor HPSS provides. Untried, and the only cheap idea found that attacks the collision in 1.1 directly |
| Pure-DSP effects add-back masks | **dead** | ~8 % music-leakage plateau. ROADMAP §11 |
| B4 mask shaping as an effects mechanism | **dead** | See 1.1. ROADMAP B4's 2026-10-18 correction |
| StemgenRT for effects | **dead** | See 1.2. Effects share the residual with melodic music |
| Wiener re-filter of effects with the model's own stems | **dead** | Under 0.5 dB change |
| TIGER-DnR int8 ONNX | **dead, but** | RTF 1.3–3.9 from 38,817 unfused ops. A re-export with fused normalisation is **untested** and might change it |

## 4. Mask-domain post-processing

Quality levers, not effects levers (1.1). They work on every model because the
mask is derived from the audio, so nothing asks a processor to change.

| idea | status | notes |
|---|---|---|
| Suppression floor for every model (`limit_db`) | **committed, default off** | Generalises `speechdenoiser`'s `atten_lim_db`. `audio/maskshape.py`, panel slider, `--limit-db` |
| Gain smoothing (`smoothing_ms`) | **committed, default off** | Smooth the wet and dry *powers* separately with one shared α, then take `sqrt(P_W / P_D)`, clamped at 1. Not the ratio — see the row below |
| Smoothing the mask *ratio* directly | **dead** | A ratio read where dry is near-silent is meaningless, and a smoothed copy survived across frames into an amplification burst dozens of frames later. Replaced by the above |
| One-sided exponent γ | **measured, abandoned** | Ruled out on `dpdfnet_hr`; on `gtcrn`/`dtln` the effect flips with how much music the source has. Not worth tuning on models about to be replaced |
| Absolute vs relative accumulator floor | **measured** | Absolute only (≈ −100 dBFS/bin). A floor relative to the frame's mean power biases every quiet bin's gain down — a correction to the reference recipe |
| Cepstral smoothing of the gain | **idea** | Breithaupt/Gerkmann/Martin. `RESEARCH_PRIOR_ART.md` §1.6 #1 |
| Look-ahead smoothing (open before onsets) | **idea** | Delay 2–4 frames so the gain opens *before* the transient. Costs 20–40 ms |
| Pitch protection for vocal harmonics | **idea** | Against high-frequency vocal loss. §1.6 #6 |
| Online REPET-SIM → Wiener post-filter | **idea** | §1.6 #5 |
| B9: blend dry back above a 6–8 kHz crossover | **idea** | Frequency-axis selectivity. Measured damage is worst in the air band (−9.1 dB whole-corpus, −13.0 on `male_lead`). Partly helps effects that live high; useless for a low thud. ROADMAP B9 |
| DeepFilterNet's post-filter `g·sin(πg/2)` | **dead** | Sharpens attenuation, i.e. *more* vocal loss |

**Open caution:** closing slow holds gain open through inter-syllable gaps,
which is where music leaks. Sparse material must be measured, not inferred —
and every B4 sweep figure before 2026-10-22 is void, because the probe's
alignment was inverted.

## 4a. Combining models rather than choosing one

The two shipped options fail in opposite directions: `dpdfnet_hr` removes
music well and chews some voices; `stemgen` leaves voices intact and leaks
audible music. Both sit on the Pareto frontier, which is exactly the condition
under which combining them can beat either.

The motivating observation is worth stating plainly, because it is the reason
to expect a blend to be useful rather than merely cheap: **switching between
the two models already feels like moving a permissiveness control.** If the
model choice is itself the knob, then the knob currently has two positions,
and a blend is the same knob made continuous. That is a far more direct route
to the control B4 was reaching for than reshaping either model's mask — and
unlike mask reshaping it adds a genuinely independent axis (two models that
disagree), which is what 1.1 says is required.

| idea | status | notes |
|---|---|---|
| Parallel blend as the permissiveness slider | **idea — the strongest unexplored lever** | Run both, mix by a ratio. **Blend per-bin gains, not waveforms**: `dpdfnet_hr` alters phase, so summing outputs risks comb filtering. Align them first (the lag estimator exists). ~4 ms of the 20 ms budget, latency of the slower. Check offline before building |
| One model's decision driving the other | **idea** | `dpdfnet_hr` as the voice-activity detector, `stemgen` as the voice content |
| Cascade: `stemgen`, then `dpdfnet_hr` on its output | **idea** | The enhancer then sees a mostly-voice signal, so its "music looks like voice" confusion matters less. +50 ms latency, ~2 ms/block. May still chew modulated voices |
| Four-stem Wiener mask | **idea** | Run `stemgen`'s drums, bass and vocals heads and use `vocals² / Σ stems²` as the gain. Standard consistency trick, ~3× compute. A Wiener re-filter did nothing for MRX effects, so expect little |
| Per-bin cleanup from the music estimate | **idea** | `input − stemgen` approximates the music; subtract with over-subtraction and a floor. Risk: spectral subtraction *creates* the metallic noise it is meant to remove |
| Voice-activity expander, a.k.a. gap gate, on `stemgen`'s output | **idea** | Attenuate frames whose voice level falls far below its running level — aimed squarely at the gap leak, which the stage videos show is where the leak is worst. Cheap, but trims breaths and quiet tails |
| Pitch comb on `stemgen`'s output | **idea** | Keep the bins on the voice's harmonics from a pitch track and attenuate other tonal bins. Attacks the leak with a genuinely independent axis (harmonicity relative to a tracked F0, per 1.1), and is the same lever as §4's pitch protection seen from the other side |

## 4b. Musical noise — the "metallic" artefact specifically

Isolated time-frequency bins that survive masking. Worth separating from the
music-leak problem, because it can be removed *without* removing the music
behind it.

| idea | status | notes |
|---|---|---|
| 2-D median or morphological opening on the gain mask | **idea** | Deletes isolated bins, keeps connected regions. Cheap and directly aimed at the mechanism |
| Temporal mask smoothing | **measured** | On `male_lead` it took `stemgen`'s musical noise from −6.5 to −10.4 (kurtosis, lower better) for 1 dB of dSI-SDR and 4 dB less music removed. On `nasheed` it barely moved — another instance of 1.3's lesson about category averages |
| Decision-directed smoothing (Ephraim–Malah) | **idea** | Classic noise-suppressor machinery, designed for exactly this |
| Mel-band gain smoothing as a post-filter | **idea** | Cheaper than a vocoder (§4c) and also cuts isolated bins |
| Track it with `musical_noise` kurtosis and `clicks` | **shipped** | So a fix can be judged before listening |

## 4c. Voice damage, and the mel domain

| idea | status | notes |
|---|---|---|
| Modulated/sung voices are what `dpdfnet_hr` chews | **under test — inconclusive so far** | `vocal_ret_db` is measured on vocals *alone*, with no music: −8.5 dB on `male_lead` against −0.9 on `sparse_acoustic`, so the damage comes from the voice and not the mix. `scripts/analyze_modulation.py` correlates per-clip damage against pitch range and speed. The first pass over 12 female clips was **inconclusive** — the strongest correlation was with *fraction voiced* at −0.41, which at n=12 is noise, and the speed measure read zero on half the clips (since fixed). The 20-clip result is pending. **Do not build the demodulation row below until this supports it** |
| Which voices `stemgen` rescues | **measured** | 12 female clips × 24 mix ratios: `stemgen` loses **no** voice on any clip, while `dpdfnet_hr` loses −3.6 dB on average and −11.4 dB at worst (Slime, Kimi no Se, Oshi no Ko). `stemgen` has the better dSI-SDR on 22 of the 24 pairs; `dpdfnet_hr` wins only on Blood Blockade. Measured on the voice alone, which is what makes this a property of the voice rather than of the mix |
| Demodulate, then de-music, then unwarp | **idea, high risk** | Warp time so pitch is near-constant, enhance, unwarp. Needs a causal pitch tracker with look-ahead, and the music warps too, smearing its pitches. Test the hypothesis above first |
| Female voices specifically | **measured** | The original corpus was 100 % female (median F0 331 Hz), above most enhancer training data — but `male_lead` showed the problem too, so it is not purely a female-voice effect |
| CleanMel / Mel-FullSubNet | **idea** | Enhance the mel spectrogram, resynthesise with a neural vocoder. Mel-FullSubNet has an online variant (15 past frames, no look-ahead). Open questions: both are speech enhancers, so they may treat music as speech too; a vocoder cannot restore what the mel discarded, including phase; licences and live CPU unchecked |
| Input-level normalisation | **idea** | `dpdfnet_hr` changes behaviour with level, and quiet input comes out silent — this gates the 0.1.4 tag. ROADMAP B10 |

## 5. Stereo image

| idea | status | notes |
|---|---|---|
| Rebuild the image from the mask rather than re-injecting side | **shipped, off by default** | ROADMAP B3 |
| Clamp the mask at 1.0 | **dead** | Retracted 2026-10-23. Fixes +17 dB anti-phase amplification, collapses the image to −235.9 dB. Mutually exclusive, 3/3 deterministic. `RESEARCH_PRIOR_ART.md` §2.8 |
| Floor the *denominator* instead | **idea** | New as of 2026-10-23. Per bin, floor the dry-mono magnitude at `k × max(L, R)` rather than letting it reach zero. An anti-phase bin then yields a mask ≤ 1 *naturally*, because its reference becomes the channel level instead of the cancelled sum — satisfying both requirements where a quotient clamp satisfies one. Untested |
| Derive the mask from mid+side energy rather than the mono sum | **idea** | The same fix from the other direction |
| Replace the mid/side ratio with smoothed spatial statistics | **idea** | As a soft prior rather than a hard prefilter. §1.6 #4 |
| Mid/side prefilter at the shipped exponent | **measured, do not enable** | Net-hurts. ROADMAP B1 |

## 6. Latency, A/V sync, and what reporting it unlocks

The highest-leverage *architectural* idea on this page, because it changes
which other ideas are possible.

| idea | status | notes |
|---|---|---|
| Report the pipeline's real latency to the graph | **idea** | Players delay video by whatever the sink reports, and the trap sink reports ~0 ms. Today's lip-sync is off by the whole pipeline |
| Use that to make a high-latency separator viable | **idea** | If a player compensates, a 1–4 s model becomes lip-sync-safe — which is what every three-way effects model needs. This is the gate on §3's best routes |
| Test whether players actually compensate (mpv, Firefox, Chromium) | **idea — do this first** | Half a day, and a decision input rather than an implementation. PipeWire 1.0.5 here would need a native `pw_filter`; Loopback `ProcessLatency` needs 1.4+ |
| Native `pw_filter` / LADSPA node | **idea** | ARCHITECTURE Phase 3. Would also fix A/V sync properly |
| Preallocated ring buffers instead of concatenate/slice FIFOs | **idea** | Reduces allocation churn in the realtime path. §2.9 |

## 7. Measurement and evaluation

| idea | status | notes |
|---|---|---|
| Three-way fixture built *by construction* | **idea** | Mix clean dialogue + clean music + clean effects at known ratios. You cannot get a three-way reference out of a two-stem separator, but mixing gives true isolated references and an effects-retention metric independent of music leak. The prerequisite for any effects work being measurable at all (1.3) |
| Ground-truth leakage test | **measured** | Insert known effects into music-only audio; measure energy outside the inserts vs retention inside. Already specified and reusable. `LIVE_SFX_HANDOVER.md` §2.2 |
| Plumbing test that drives the real engine | **committed** | `tests/test_engine_chunked.py`. Its absence is why a 16 %-processed defect survived a whole sweep — the bench reimplements the chain instead of driving the engine |
| Per-category reporting rather than corpus averages | **shipped** | A config that wins on average can fail badly on one category |
| Held-out clips never used while tuning | **shipped** | The harness itself becomes something to overfit to |
| Per-clip tables rather than averages | **shipped** | `scripts/bench_subset.py` |
| Stage videos: spectrograms, level curves, model-vs-oracle map | **shipped** | `scripts/visualize_stages.py`. Showed `stemgen`'s leak is worst in vocal gaps and the top octave, with horizontal lines marking tonal music |
| Dump the chain's inner stages into the same video | **idea** | After downmix, after model, after band-limit, after shaper — so a defect can be localised to a stage instead of inferred |
| Wide-stereo and out-of-phase corpus | **idea** | The stereo rebuild's failures all live in material the current corpus barely contains — see §5 |
| Modulation analysis per clip | **shipped** | `scripts/analyze_modulation.py` — pitch range, speed and fraction voiced, correlated against measured damage. See §4c for what it has and has not shown |
| Pipeline diagrams per version | **shipped** | `docs/PIPELINE_HISTORY.md` plus six SVGs in `docs/pipeline/`, generated by `scripts/draw_pipelines.py`. New boxes green, changed boxes amber, relative to the previous version |
| Listening sets and stage videos on disk | **shipped** | `~/.local/state/music-assassin/bench/listen/` — `audio_male/` (8 clips) and `female/audio/` (12) each holding the original, `stemgen`, `dpdfnet_hr`, the offline separator and the ideal-mask reference. 16 male videos in `video/male/`, one audio track per stage (`#` switches them in mpv), with a model-versus-reference map showing leak red and cut voice blue |
| Blind A/B re-ranking of models | **idea** | `scripts/ab_listen.sh`. The sighted ranking put `dpdfnet` third despite it being the only net-harmful model |
| Synthetic tones as a music fixture | **dead** | Suppressed like noise (−47 dB) against real music's −0.4 to −1.7 dB. Guarded against in `test_live_e2e.py` |

## 8. Plumbing and platform

| idea | status | notes |
|---|---|---|
| Fail-closed mode | **idea** | Every failure path currently emits dry, i.e. leaks the music. `RESEARCH_PRIOR_ART.md` §2.4 |
| Data-driven model registry from model cards | **idea** | ROADMAP A3. Prerequisite for a first-run downloader |
| WirePlumber smart filters instead of UI polling | **idea** | Turns a 1 s `wpctl`/`pw-dump` poll into events |
| Rootless system-wide Android processing | **feasible, not planned** | RootlessJamesDSP's capture-and-silence pattern. ARCHITECTURE §6's 2026-10-22 correction |
| Equalizer APO / native APO on Windows | **idea** | Needs the Phase-3 C++ port first |
| Synchronous Audio Router as a VB-CABLE replacement | **dead** | Needs an ASIO host, worse unsigned-driver UX, licence surface |
| OpenVINO execution provider for the sherpa path | **dead** | ~16 % faster, but sherpa statically links its own onnxruntime |
| Self-quantising UVR models to int8 | **dead** | 3.7× *slower* — Conv2D/STFT-bound, not MatMul-bound |

## 9. Ideas that existed only in conversation until now

Recorded because they were reasoned about at some length and would otherwise
be lost:

- **Periodicity as the drum/effect discriminator** (§3). The one cheap,
  untried idea that attacks the collision in 1.1 head-on.
- **HPSS as the second axis** (§3) — and why it fails.
- **Denominator flooring for the stereo mask** (§5).
- **Three-way fixtures by construction** (§7).
- **"De-music smaller samples at a time."** Raised as a way to stop effects
  being swallowed with the music. **Blocked, and worth knowing why:** the
  STFT frame and hop are baked into each model's architecture and training,
  so a model cannot be asked for finer-grained decisions from outside. A
  parallel *short-window* path that bypasses the model is possible, but that
  is pure-DSP add-back again, i.e. the ~8 % plateau. The intuition is sound;
  the lever does not exist at this layer.
- **"Make the de-musicing less strict."** A single model's strictness is its
  training objective, not a parameter, so it cannot be loosened from outside.
  But the idea has two real realisations, and an earlier version of this page
  wrongly called it dead: **blend two models in parallel** (§4a) to trace
  points between a permissive one and a strict one, and **change what gets
  classified** — predict music and subtract it (A4), so effects survive
  because nothing ever labelled them.

## 10. Where to add the next idea

Put it in the section for the problem it addresses, with a status from §0 and
one line on why it might work. If it dies, **leave it here and write down what
killed it** — then check whether the reason generalises into a new principle
for §1. Three of the four principles there started as a single dead idea.
