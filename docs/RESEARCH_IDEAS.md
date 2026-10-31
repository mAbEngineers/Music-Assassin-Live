# Research ideas: directions to try when stuck

Every direction raised so far, followed or not. One or two lines each: what, why it might work, what it costs, what is known. Status tags:

- **built**: in the code.
- **measured**: tried, with numbers (where).
- **untested**: no evidence yet.
- **rejected**: ruled out, with the reason. Check the reason before re-opening.
- **blocked**: needs something else first.

Context: the strongest-suppression model (`dpdfnet_hr`, the default until 2026-10-31; `stemgen` is now) removes music well but chews some voices. The causal separator (`stemgen`) keeps voices intact but leaves audible music. Numbers: ROADMAP A2 and B4.

## 1. Less leftover music from `stemgen`, voice unchanged

- **Sound the problem out first.** `visualize_stages.py` videos show where it leaks. On the clip checked, the leak is worst in vocal gaps and the top octave, and horizontal lines mean tonal music. **measured**
- **Cascade: `stemgen`, then `dpdfnet_hr` on its output.** The enhancer sees a mostly-voice signal, so its "music is voice" confusion matters less. Costs +50 ms latency and ~2 ms per block. May still chew modulated voices. **untested**
- **Voice-activity expander on `stemgen`'s output.** Attenuate frames where the voice level falls far below its running level. Aimed at the gap leak. Side-chain it from `dpdfnet_hr` or `stemgen` itself. Cheap, but trims breaths and quiet tails. **untested**
- **Stricter shaping** (`exponent > 1`) on `stemgen`. Only the permissive settings (floor, release) were swept, and those lowered dSI-SDR. **untested**
- **Four-stem Wiener mask:** run `stemgen`'s drums, bass and vocals models, take `vocals² / (sum of stems²)` as the gain. A standard consistency trick. Costs ~3× compute. A Wiener re-filter did nothing for MRX effects (offline work), so expect little. **untested**
- **Per-bin cleanup from the music estimate:** `input − stemgen` approximates the music; subtract it with over-subtraction and a floor. Risk: spectral subtraction creates the metallic noise it is meant to remove. **untested**
- **Pitch comb:** keep bins on the voice's harmonics (from a pitch track), attenuate other tonal bins. Also the lever for high-frequency vocal loss. RESEARCH_PRIOR_ART §1.6 item 6. **untested**

## 2. The "metallic" noise specifically

It is musical noise: isolated time-frequency bins that survive masking. It can be removed without removing the music behind it.

- **2-D median or morphological opening on the gain mask**, which deletes isolated bins and keeps connected regions. **untested**
- **Temporal mask smoothing.** The shaper's release smoothing already does part of this. On `male_lead` it took `stemgen`'s musical noise from −6.5 to −10.4 (kurtosis, lower is better) at the cost of 1 dB dSI-SDR and 4 dB less music removed. On `nasheed` it barely moved. **measured** (ROADMAP B4)
- **Decision-directed smoothing** as in classic noise suppressors (Ephraim–Malah), which was designed for this. **untested**
- **Track it with the existing `musical_noise` kurtosis and `clicks` metrics**, so a fix can be judged without listening first. **built**

## 3. Voice damage from `dpdfnet_hr`

- **Modulated or sung voices (user hypothesis):** the model may be tuned to speech's pitch dynamics. Evidence so far: `vocal_ret_db` is measured on the *vocals alone*, with no music, and reads −8.5 dB on `male_lead` against −0.9 on `sparse_acoustic`. So the damage is not caused by the mix. Whether *pitch modulation* is what drives it is untested: correlate per-clip damage with pitch range and speed. **untested**
- **Demodulate, then demusic:** warp time so the pitch is near-constant, run the speech enhancer, unwarp. Needs a causal pitch tracker with look-ahead, and the music is warped too, so its own pitches smear. High risk. Test the hypothesis above before building. **untested**
- **Girls' voices ("cuts some girl vocals", 2026-07-24):** the original corpus was 100 % female (median F0 331 Hz), above most enhancer training data. `male_lead` showed a male-voice problem too, so it is not purely a female-voice one. **measured** (ROADMAP corpus notes). `stemgen` on those clips: see CURRENT_STATE.
- **Seed the model state properly.** DPDFNet's state was zero-started, discarding its calibration. **built** (A5)
- **Input level normalisation:** `dpdfnet_hr` changes behaviour with input level, and quiet input comes out silent. **untested** (ROADMAP B10)
- **Give the top octave back:** blend dry above a crossover. `dpdfnet_hr` loses 6 dB in the air band. Does nothing for the 16 kHz models. **untested** (B9)
- **Mid/side centre focus:** 1.8 dB of suppression at best. **measured**, kept off.

## 4. Combining models

- **Parallel blend as a permissiveness slider (user idea):** run `stemgen` and `dpdfnet_hr`, mix their outputs by a ratio. Cost is the sum of both (~4 ms of 20), latency is the slower one's 50 ms. Align them first (the lag estimator exists). Blend per-bin gains, not waveforms: `dpdfnet_hr` changes phase, so summing waveforms risks comb filtering. Both models are on the Pareto frontier, so a ratio should trace intermediate points. Check that offline before building. **untested**
- **Use one model's decision to drive the other:** `dpdfnet_hr` as the voice-activity detector, `stemgen` as the voice content. **untested**
- **Gate on music presence:** pass dry through when no music is playing, which keeps all effects. LIVE_SFX_HANDOVER §4.1. **untested**

## 5. Heavy models live

- **Demucs-class models are non-causal** and need seconds of context. The same is true of MRX and CDX23. **measured** (Spleeter: quality keeps rising through 4 s windows, never flattens)
- **"Movie mode":** run them with a deliberate 0.5–1.5 s delay that the player is told about, so the picture is delayed to match. **blocked** on latency reporting: this machine has PipeWire 1.0.5 (loopback latency needs 1.4+), and nobody has checked that mpv, Firefox or Chromium actually re-time the video.
- **Browser companion:** process audio ahead of the playhead and play it back through Web Audio. No realtime limit, so offline models run unchanged. RESEARCH_PRIOR_ART §1.5. **untested**
- **OpenVINO with BF16** gave the offline app a 2–3× speed-up on one large model. The live notes only tested OpenVINO through sherpa-onnx. Relevant only if a large model goes live. **untested**
- **Native `pw_filter` node** for latency reporting on PipeWire 1.0.5. **untested**

## 6. Cleaner voice via the mel domain

- **CleanMel and Mel-FullSubNet (user suggestion):** enhance the mel spectrogram, then resynthesise with a neural vocoder. Both are from Westlake University and target noise and reverb. Mel-FullSubNet has an online variant (15 past frames, no look-ahead). CleanMel uses a modified Vocos vocoder, and its paper says the original Vocos is non-causal. **untested**
  - Open questions: they are speech enhancers like `dpdfnet_hr`, so they may treat music as speech too; a vocoder cannot restore what the mel discarded, which includes the phase; licences and live CPU cost are unchecked.
  - Sources: [CleanMel](https://arxiv.org/abs/2502.20040), [Mel-FullSubNet](https://arxiv.org/abs/2402.13511).
- **Mel-domain masking as a post-filter** (cheaper than a full vocoder): smooth the gain over mel bands, which also cuts isolated bins. **untested**

## 7. Training our own

- **Causal "music vs everything else" model** trained on our corpus. The only route to keeping effects at 20–50 ms. LIVE_SFX_HANDOVER §4.4. **untested**
- **Retrain GTCRN or DPDFNet** with clean = dialogue + effects, noise = music. No app changes needed. **untested**
- **Slow separator as an online teacher** for a fast student. **untested**
- **Fine-tune `stemgen`** on anime-opening vocals if its licence and data allow. **untested**
- **Hybrid: a small causal model on top of `stemgen`** that predicts a cleanup mask. **untested**

## 8. Sound effects

- **Do not re-run:** mask shaping as an effects mechanism, pure-DSP add-back, a Wiener re-filter of effects, `stemgen` for effects. All **rejected**, reasons in LIVE_SFX_HANDOVER §6.
- **Classifier on the residual** (`dry − wet`) to add back non-music windows. YAMNet got 0.5 % leakage at 94 % effects kept offline. **untested** live.
- **MRX with look-ahead** (movie mode), **CDX23 as teacher.** **untested**

## 9. Evaluation and tools

- **Per-clip tables, not averages:** `scripts/bench_subset.py`. **built**
- **Stage videos** with spectrograms, level curves and a model-versus-oracle map: `scripts/visualize_stages.py`. **built**
- **Dump the chain's inner stages** (after downmix, model, band-limit, shaper) and show them in the same video. **untested**
- **Blind A/B listening:** `scripts/ab_listen.sh`. **built**, re-aim it at the new dump folders.
- **Wide-stereo and out-of-phase corpus** for the stereo rebuild. **untested**
- **Per-category splits of every sweep.** Averages hid a ratio-dependent effect once already. **built** (partly)

## 10. Plumbing

- **Reporting latency to players**, see section 5. **blocked**
- **Fail closed:** when the chain fails, mute rather than leak the music. RESEARCH_PRIOR_ART §2.4. **untested**
- **Preallocated ring buffers** instead of `np.concatenate` FIFOs in the callback. **untested**
- **Native node** (Phase 3) for the real-time path. **untested**
