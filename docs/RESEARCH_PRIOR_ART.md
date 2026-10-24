# Prior Art, Ideas, and a Review of the Current Pipeline

Researched 2026-10-18. Two parts:

1. **Prior art** — similar projects, products, models, plumbing and DSP
   techniques, and what this app can take from each.
2. **Pipeline review** — the live chain as it stands on
   `feat/separator-spike`, and the practices in it that are costing quality
   or correctness.

Companions: [ROADMAP.md](ROADMAP.md) (plan, measured findings, dead ends),
[ARCHITECTURE.md](ARCHITECTURE.md) (layering and contract). Read ROADMAP §2
and §11 first — nothing here re-recommends anything already measured and
rejected there.

**How much to trust each claim.** Every project named was opened (repo, store
listing, paper or source file). Claims are tagged:

| tag | meaning |
|---|---|
| **[measured]** | run on the dev machine during this research |
| **[source]** | read in the project's source code, docs or NEWS |
| **[vendor]** | the project's own claim, not independently checked |
| **[estimate]** | derived here, not published anywhere |

APKPure returns HTTP 403 to automated fetches, so Android apps were found via
Google Play and search engines instead.

---

## Part 1 — Prior art

### 1.1 The five findings that matter most

| # | finding | what it changes |
|---|---|---|
| 1 | A causal music separator with **public weights** now exists: StemgenRT v0.7.0 | ROADMAP A2 ("HS-TasNet: no public weights") is out of date; there is now something to sweep against `dpdfnet_hr` |
| 2 | Players delay video by whatever latency the sink **reports**, and our null sink reports zero | Today's lip-sync is off by the whole pipeline; and Q3 is not "50 ms or nothing" — a graph-linked node reporting its latency could make even a 1–4 s separator lip-sync-safe in video players |
| 3 | Smoothing **powers** instead of the `\|Wet\|/\|Dry\|` ratio fixes the unresolved B4 bug | Unblocks `attack_ms`/`release_ms` |
| 4 | Gating on **voice presence** targets the music-only-interlude failure directly | A cheap lever for `long-burst`/`leak-burst` |
| 5 | The leading Muslim-market tools **process ahead of the playhead** rather than in real time | An optional browser companion could run a heavy separator with zero added latency on YouTube |

### 1.2 Realtime separation models

#### StemgenRT v0.7.0 — try first

- Repo: <https://github.com/sweetspotsoundsystem/stemgen-rt> (plugin + ONNX,
  MIT, created 2026-01, v0.7.0 released 2026-10-11, 49★).
  Training code: <https://github.com/sweetspotsoundsystem/StemgenRT-5.8>
  (MIT; formerly `sweetspotsoundsystem/HS-TasNet`, a fork of lucidrains'
  HS-TasNet).
- Release asset `model.onnx`: one 32 MB FP32 file, opset 17, standard ops
  only (including DFT). Three independent sub-models: drums, bass, vocals.
  SHA-256 `0a49388dd2a47986ab9a161c8764c22853b4d601c188ac052eb530f076d513c7`
  matches the published `SHA256SUMS.txt` **[measured]**.
- Interface: input `audio_chunk [1, 2, 128]`, stereo 44.1 kHz, plus
  recurrent state tensors carried between calls. One-hop delay
  (128 samples), 1024-sample lookback → **≈5.8 ms algorithmic latency**.
- **Vocals subgraph can be cut out** with `onnx.utils.extract_model`:
  inputs `audio_chunk` + `vocals_{audio_history, local_hidden,
  global_hidden, spectral_numerator_tail}`, outputs `/vocals/Div_2_output_0`
  + the four `next_vocals_*`. Result: 11 MB, 2.72M parameters, output
  **bit-identical** to the full graph's vocals **[measured]**.
- Speed, ORT 1.31, Ryzen 9 9950X3D, 1 thread, spinning off: **0.40 ms per
  128-sample hop, RTF 0.14** (p99 0.48 ms); full 3-stem graph 1.21 ms
  **[measured]**. A 20 ms block is ≈7 hops ≈ 3 ms. The app's own machine
  may be slower — re-measure there.
- Quality, quick check: 50 MUSDB18 test excerpts (7 s, AAC stems, global
  SDR, not museval cSDR): vocals median **5.32 dB** vs **−4.49 dB** for the
  untouched mix **[measured]**. The maintainers mark v0.7.0
  `quality_measured=False`; v0.6.2 scored 5.45 dB vocals on their own
  14-track panel **[vendor]**. That is HS-TasNet class (paper: 5.13 dB).
- Integration: `wants_stereo = True`, `sample_rate = 44100` (the engine's
  soxr pair already handles 48k ⇄ 44.1k stereo), an internal 128-sample hop
  FIFO under the 20 ms blocks, states zeroed on `reset()`, first call's
  output treated as pre-roll.
- **Caveats.**
  - Not yet run through `bench_quality.py`, so nothing is known on the
    anime-OP or nasheed corpora, and spoken dialogue is untested (a MUSDB
    model may or may not call speech "vocals").
  - Trained on MUSDB18-HQ + MoisesDB, both **non-commercial** datasets. The
    weights are MIT-labelled, so the OSI gate passes, but the dataset terms
    are a risk worth writing down before shipping.
  - It uses almost none of the ~120 ms budget — the obvious lever for more
    quality is a longer analysis window (below).
  - **It does not keep SFX, and the stem set is why.** Three heads means no
    `other` and no concept of an effect, so SFX and melodic instruments share
    the unmodelled residual `R = mix - vocals - drums - bass`. The only
    control that exists is `vocals + alpha*R`: one scalar trading effects
    against backing music — not selectivity, and structurally the same trade
    the B4 mask knob offers from a worse baseline. A MUSDB18-trained model has
    most likely filed an explosion under *drums*, the stem being discarded, so
    the drums-look-like-SFX collision in ROADMAP §11 relocates into the
    separator rather than clearing. This matters for ARCHITECTURE §7, which
    conditioned music-vs-SFX separation on "HS-TasNet weights": the
    precondition is now met and the conclusion is unchanged.

Local copies (outside the repo, session scratchpad, may be cleaned up):
the extracted `vocals_only.onnx` and the extraction/benchmark scripts were
written under the Claude session's scratchpad `model_dl/` and `scripts/`.
Regenerate from the release asset with the recipe above if they are gone.

#### Train our own causal model — best quality per CPU

Published causal results show quality climbing steeply once latency is
allowed to reach ~93 ms (one 4096-point window at 44.1 kHz):

| model | latency | vocals SDR | params | weights? | notes |
|---|---|---|---|---|---|
| Band-SCNet (Interspeech 2025) | 92 ms | 8.74 dB (avg 7.79) | 2.59M | no | RTF 0.478 on one i7-13700H thread, measured on 11 s segments, so streaming will cost more. [Paper](https://www.isca-archive.org/interspeech_2025/yang25d_interspeech.pdf) |
| Online SCNet (same paper) | 92 ms | 8.53 dB | 4.36M | no | causal convs + cumulative LayerNorm + unidirectional LSTM |
| RT-STT (ICME 2025, [arXiv 2511.13146](https://arxiv.org/abs/2511.13146)) | 23 ms | 5.56 dB | 383K | no | repo `Jundo26/RT-STT` is a README saying "code will be released soon", last push 2025-03 |
| HS-TasNet (ICASSP 2024, [arXiv 2402.17701](https://arxiv.org/abs/2402.17701)) | 23 ms | 5.13 dB (5.97 with extra data) | 42M (small: 16M) | no | 9.1 ms per 23 ms on 1 core of an i7-12850HX |
| Analog Devices DSP model ([arXiv 2609.12201](https://arxiv.org/abs/2609.12201)) | ≈46 ms | 5.09 dB | 131K | no | training tip: train on continuous context or streaming collapses |
| Deep Latent Masking (Interspeech 2023) | 23 ms | 6.91 dB (MUSDB18, not HQ) | 5.7M | no | — |

Routes:

- **SCNet → causal**: <https://github.com/starrytong/SCNet> (MIT); swap in
  causal convs, cumulative LayerNorm, unidirectional LSTM as the Band-SCNet
  paper describes. An unofficial WIP exists at
  `olivepicker/band_scnet_pytorch` (no license file — do not copy).
- **Reuse the StemgenRT-5.8 trainer** (MIT): already has carried-state
  warmup, source-absence losses, an ONNX exporter and an optional 4096-point
  vocal-analysis variant.
- **Distill on our own material.** This repo already builds htdemucs
  reference stems for the corpus; the same pipeline can label anime OPs and
  nasheeds as teacher targets. ZFTurbo/Music-Source-Separation-Training
  (MIT) has a teacher–student dataset type. Published music distillation
  so far is offline only (Mel-RoFormer 12.12 → student 11.17 dB,
  [arXiv 2510.25745](https://arxiv.org/abs/2510.25745)). Amazon's Fire TV
  Dialogue Boost used exactly this pattern (small sub-band model, under 1 %
  of the teacher's size, trained on real media labelled by the big model)
  **[vendor]**.
- **Open-Unmix** (`sigsep/open-unmix-pytorch`, MIT) has a documented
  `--unidirectional` training flag ("causal model useful for realtime").
  Released weights are bidirectional. The only causal UMX checkpoint found
  (`shuoyang-zheng/Open-Unmix-Unidirectional`) ships as Apple-only
  ExecuTorch/MLX files.

#### Streaming an unmodified offline model — a ceiling, not a product

Sechet et al. ([arXiv 2609.35397](https://arxiv.org/abs/2609.35397), code
<https://github.com/dylansechet/mss_streaming>, MIT) slide the full context
window forward one hop at a time and read out a single hop:

| model | vocals @ 23 ms | vocals @ 93 ms | RTF (RTX 5080 GPU) |
|---|---|---|---|
| DTTNet (vocals, 5M) | 8.10 dB | 9.69 dB | 1.57 |
| SCNet | 7.45 dB | 8.67 dB | 1.59 |
| HT-Demucs | 6.90 dB | 8.49 dB | 0.89 |

Not CPU-realtime. Useful because it explains this repo's Spleeter result:
chunks without context lose quality, and keeping context costs a full
forward pass per hop.

#### Everything else checked

| name | status for this app |
|---|---|
| lucidrains/HS-TasNet (MIT) and ports (ovistetom, temismink, NeutoneFX) | training code, `sounddevice` streaming demo; **no weights** (issues #1–7) |
| Cadenza CAD2 `ConvTasNet_Lyrics_Causal` ([HF](https://huggingface.co/cadenzachallenge/ConvTasNet_Lyrics_Causal), Apache-2.0) | the only ready-made causal 2-stem vocals checkpoint; ~10.9M params, ~46 GMAC/s **[estimate]** ≈13× HS-TasNet; no published SDR; needs ONNX export + conv state caching. Low priority |
| Moises-Light (WASPAA 2025) | offline (6–9 s chunks), 10.92 dB, no weights |
| MMDenseNet realtime (arXiv 2407.00657) | ≥250 ms, accompaniment only, no weights |
| Vox-TasNet (Apple, arXiv 2401.12068) | limited lookahead karaoke cancellation, no weights |
| LiteCASS (arXiv 2609.23453) | dialogue/music/effects split — would send singing to "music" |
| sherpa-onnx source-separation models | still only Spleeter and UVR MDX |
| sevagh demucs.cpp / demucs.onnx / umx.cpp / free-music-demixer | offline (MIT); umx.cpp's "streaming LSTM" is chunked-bidirectional, not causal; free-music-demixer archived |
| Mixxx GSoC 2025 HTDemucs→ONNX, HF ONNX exports | offline, 7.8 s segments |
| Intel OpenVINO Audacity plugin | offline htdemucs, GPL-3.0 (fails bundling gate) |
| python-audio-separator(-live) | window-overlap streaming ≈1.7 s on GPU — same idea as our Spleeter test |
| Yash-Bhake/RT-MSS | hobby causal BSRNN checkpoint, not usable |
| Speech enhancers retrained on MUSDB for singing | **none found** (DeepFilterNet, GTCRN, DTLN, FullSubNet issue trackers checked) |

Dataset licensing applies to everything trained on public multitrack data:
MUSDB18-HQ is "educational, non-commercial"; MoisesDB is CC BY-NC-SA 4.0;
MedleyDB tracks are BY-NC-SA.

### 1.3 Similar products and projects

#### Closest competitors

| project | what it is | takeaway |
|---|---|---|
| **Poise Voice Isolator** — <https://github.com/chabandou/poise-voice-isolator> (MIT, 20★, pushed 2026-10-17) | Nearly this product: system-wide, halal-oriented, native PipeWire node with a fixed-delay core, DeepFilterNet3 default plus GTCRN/RNNoise, Windows via VB-Cable, Android (Samsung only, via "Separate app sound" + GTCRN) | Same speech-enhancer quality ceiling. Claims "~10 ms" but its README lists 32 ms model delay + 21 ms margin. Possible collaborator |
| **Moises Live** | Commercial system-wide stem control (vocals / instruments / dialogue), "Music" and "Movie/TV" modes; macOS 15 on M1+ and Windows on Snapdragon X; free | Proof the product works with a real separator + an NPU (Moises: 35× faster on Snapdragon NPU than CPU **[vendor]**). No Linux, no published latency, reviews mention faint vocal bleed |
| **NoMusic** — <https://github.com/mohamedsadiqaguenaou/NoMusic> (ISC, 20★) | Chrome extension, real-time on tab audio, FastEnhancer ONNX (ORT Web) + RNNoise WASM, ~1K users | Same model family as ours, browser-only |
| **UnTube** — `reedwan/UnTube` (no license) | Chrome, causal custom-trained FastEnhancer-M + expander + 9.5 kHz low-pass | Its "+24.3 dB SDR" claim is **[vendor]** and does not fit our ~1 dB measurements of that model class |
| **Haram Music Remover** (musicremover.org) | Chrome/Firefox + local desktop helper (Win/macOS/Linux .deb/AppImage); chunked processing ahead of playback, 5–10 s start, follows seeks, caches; ~3K users, 4.8★ | Closed source, model unnamed. Gives up real-time to win quality |
| **HaramMute** (haram-mute.com) | Chrome ~30K users, Android 50K+ installs, iOS; cloud "prepare" step, 60 min/week free, videos ≤15 min, no live streams | Owns the user base. Reviews mention sync drift and missed instruments on an ibtihal |
| **Samsung Galaxy S26 Audio Eraser** (One UI 8.5) | Real-time inside YouTube/Netflix/Instagram, strength slider + Voice Focus | Platform-locked; nobody has tested it on music specifically |

#### Muslim-oriented tools (beyond the above)

| name | real-time? | technique | open source |
|---|---|---|---|
| YouTube Music Remover — <https://github.com/Amro-AL-Ghamdi/musicremover> | no — lookahead from YouTube's buffer | Chrome extension + local Python GPU server; UVR Voc FT for voice + DnR Demucs (speech/music/effects) for effects, combined so both singing and SFX survive | MIT, 2★ — the best-documented lookahead design |
| Murem — <https://github.com/Ignema/youtube-music-remover> | no — job queue | Android (F-Droid), web, Colab; Kim Vocal 2, MDX, RoFormer | MIT, 75★ |
| Moubah (itkho) | no | Electron + Python gRPC, probably Spleeter | AGPL, archived 2026-02 |
| Aswaat / Sawtunaa, Porda AI | claimed | unclear | unverified, ~0 users |

Treat as suspicious: `Toinetteindecent428/youtube-music-remover` (random
account mirroring another project) and `Moises-Live-Vocal-Remover/.github`
(SEO spam, not official).

#### Android

- System-wide processing **without root is possible**, contrary to
  ARCHITECTURE.md §6. Reference: **RootlessJamesDSP**
  (<https://github.com/timschneeb/RootlessJamesDSP>) **[source]**:
  - captures other apps with `AudioPlaybackCaptureConfiguration`
    (USAGE_MEDIA, GAME, UNKNOWN) and replays through its own `AudioTrack`;
  - **silences the originals** by attaching
    `DynamicsProcessing(Int.MAX_VALUE, sid, null)` with input gain −200 dB to
    each app's audio session (fallback: a hidden Volume effect), found via
    the `DUMP` permission (`MutedAudioEffectFactory.kt`);
  - one-time setup over ADB or Shizuku: `pm grant <pkg>
    android.permission.DUMP` and `appops set <pkg> PROJECT_MEDIA allow`;
  - limits: apps that opt out of capture stay unprocessed (Spotify, Chrome,
    SoundCloud), AAudio apps can't be patched, conflicts with other
    DynamicsProcessing apps, added latency; Android 15 needs "Disable screen
    share protection".
  - Verdict: feasible for the low-latency path on capturable apps; a
    high-latency separator breaks lip-sync there because the player's A/V
    clock never sees the extra delay.
- Play-store "vocal remover" apps (unMix, VOIX, SonicMelody, SplitHit,
  io.jkhddev "AI Vocal Remover Offline", info.hijo "Vocal Remover") are all
  offline splitters for the user's own library, whatever their listing says.

#### Streamer / DMCA tools

No real-time AI music-removal OBS plugin exists. YouTube Studio "Erase Song"
is offline, after a claim. StreamHush recognises songs and mutes the music
source (~2 s), no separation. Standard practice is a separate VOD audio
track. Broadcast SDKs (AudioShake Dialogue RT, 11 ms on NVIDIA Blackwell;
CEDAR Voxis) target dialogue vs crowd, not music **[vendor]**.

#### TV / streaming dialogue enhancement

All real-time AI dialogue separation, none publish latency. Amazon Fire TV /
Echo Dialogue Boost (on-device, any app, Amazon Science blog Dec 2025);
Samsung Active Voice Amplifier Pro ("may not be supported" in Game Mode —
consistent with hiding inside the TV's video delay); Sony Voice Zoom 3; LG
α11 AI Voice Remastering; Apple TV Enhance Dialogue (ML since tvOS 18); Roku
Speech Clarity; Fraunhofer Dialog+ (≈370K params, trained on 15 h of
hand-cleaned broadcast stems; file-based; their paper calls live production
"a remaining challenge"); Dolby AC-4 (encoder sends per-band dialogue gains).

**The pattern:** TVs control the video path, so they spend 40–100 ms of
model latency inside their own picture delay. A PC can do the same only if
the audio path reports its latency (§1.4).

#### DJ software

Serato, VirtualDJ Stems 2.0, djay Neural Mix, rekordbox 7, Traktor Pro 4,
Denon Prime 4+ — all have the whole file, so "real-time" means processing
ahead of the playhead or pre-analysis. VirtualDJ falls back to its fast
"stems 1.0" engine when a track isn't prepared. Mixxx: GSoC 2024 added NI
stem playback, GSoC 2025 converted Demucs v4 to ONNX; no on-the-fly
separation found.

#### Pro plugins (real-time)

Acon Digital Remix (5 stems, latency unquantified), iZotope RX 12 real-time
Music Rebalance and Dialogue Isolate (real-time + offline HQ modes), Waves
Clarity Vx Pro (≈42 ms per a competitor's figure), Supertone Clear (Eco
mode, ≈245-sample offset per users), Audionamix IDC, Zynaptiq UNMIX::DRUMS.

#### Speaker-side AI on PCs

NVIDIA Broadcast and Krisp both install a virtual **speaker** device that
apps target and both advise against making it the system default; music is
not in their target lists. SteelSeries Sonar ClearCast on the chat channel.
Apple Music Sing: on-device "real time" vocal reduction, iPhone 11+.

#### Transferable ideas, in short

1. **Let the player hide the delay** — report latency (§1.4).
2. **Look ahead when the source is a file or buffered stream** — browser
   companion (§1.5).
3. **Tiered fallback** — fast path when the heavy one isn't ready
   (VirtualDJ), but fall back to *quiet*, not to the original (see Part 2 §2.3).
4. **Train small and causal on the actual target material** (Amazon,
   Fraunhofer, distillation in §1.2). Stock speech enhancers never had
   singing in their training target, which is why nasheeds fail.
5. **Remix, don't only mask; gate on voice activity** — Dialog+ ducks the
   background extra only while someone speaks; Torcoli & Habets (ICASSP
   2023) move leakage into the background when nobody speaks.
6. **Strength control with a hard-mute preset** — Prime Video Low/Med/High,
   Samsung slider, Apple Music Sing never fully removes. This app's
   religious audience needs the hard preset; others want the slider (C12).
7. **Use an accelerator when present** — ORT CUDA / ROCm / OpenVINO could
   buy a larger model at the same hop.
8. **Put the toggle where people already look** — quick-settings tile, tray,
   a status colour (UnTube's grey/amber/red).

### 1.4 Plumbing: latency reporting, native hosting, other platforms

#### Latency reporting and A/V sync — the most consequential finding

- **Players do compensate for reported latency [source]:**
  - Chromium passes `pa_stream_get_latency()` into
    `OnMoreData(BoundedDelay(delay))`, clamped to 0–10 s
    (`media/audio/pulse/pulse_util.cc`, `media/audio/audio_io.cc`).
  - Firefox / cubeb (`pulse` and `pulse-rust`) take position from
    `pa_stream_get_time()`.
  - mpv `ao_pipewire` adds `time.delay`; `ao_pulse` uses
    `pa_stream_get_latency`.
  - VLC master: `pa_stream_get_time()` → `aout_TimingReport`.
  - PipeWire adds downstream `Latency` params into `pw_stream_get_time_n()`
    → `time->delay`, and pipewire-pulse reports it in
    `GET_PLAYBACK_LATENCY`.
  - Multi-second precedent: PipeWire's RAOP (AirPlay) sink reports
    `raop.latency.ms` (default 1500) + 250 ms as ProcessLatency.
- **Our null sink reports nothing.** `support.null-audio-sink` has no latency
  code (zero occurrences of "latency" in `null-audio-sink.c` **[source]**),
  and the PortAudio capture → separate playback hop ends the graph there. So
  apps see ≈0 ms while the real delay is ≈50 ms pipeline + ≈70 ms PortAudio
  buffering + Bluetooth. Same bug class as EasyEffects #2951 /
  PipeWire #3888.
- **Proper fix:** make processing a node *inside* the graph (filter-chain,
  `pw_filter`, or loopback) that publishes `ProcessLatency`; hardware and
  Bluetooth latency then propagate automatically. EasyEffects does this
  (`monitor.passthrough=true` on its sink, `spa_process_latency_build(...)`
  from its filters).
- **Interim hack keeping Python (PipeWire ≥ 1.4.0):** front the internal
  null sink with a loopback sink (`capture.props = { media.class=Audio/Sink
  node.name=MusicAssassin }`, playback → internal null sink), then
  `pw-cli set-param <capture-node-id> ProcessLatency '{ ns = <total> }'`.
  NEWS confirms "Streams now have support for ProcessLatency" and "The
  loopback module and module-raop have support for ProcessLatency"
  **[source]**. Hardware/BT latency must be added by hand in this variant.
  **Tested and rejected on 1.0.5** (`-95 Operation not supported`) — the
  current baseline (Ubuntu 24.04) cannot use it.
- **Dead ends:** `pactl set-port-latency-offset` only applies to card routes;
  `latency.internal.rate` / `latency.internal.ns` are ALSA-node-only.
- **Consequence for Q3:** with honest latency reporting, a 1–4 s separator
  mode would stay in lip-sync in browsers and video players (not in calls or
  games). Not yet tested hands-on — do that before designing around it.

#### Native hosting

- **WirePlumber 0.5 smart filters** (`filter.smart = true`,
  `filter.smart.name`, optional `filter.smart.target` / `before` / `after`)
  insert a filter between streams and the default device *without* changing
  the default; toggle with `pw-metadata -n filters <id>
  filter.smart.disabled true Spa:String:JSON`. Removes the need to hijack
  the default sink and re-assert it every second, and keeps BT latency
  flowing. Needs WirePlumber 0.5.
- **filter-chain plugin types in PipeWire 1.6:** `ladspa`, `lv2`,
  `builtin`, `sofa`, `ebur128`, `ffmpeg`, `onnx`. "Latency and
  ProcessLatency support was added to filter-graph" and "A new ONNX filter
  was added to filter-graph" (NEWS, 1.5.81) **[source]**.
  - The ONNX plugin runs `ort->Run` synchronously in the RT callback, single
    thread, raw samples only (any STFT must be inside the model), reports no
    latency, and is only built with `onnxruntime=auto` — Arch's package does
    **not** ship it. Good for VAD-sized models, not a separator.
- **LADSPA latency:** expose an output control port named exactly `latency`
  (samples); filter-graph tags it and, from 1.6, turns it into ProcessLatency.
  DeepFilterNet's LADSPA has **no** such port, so it reports 0 despite a
  ≥20 ms STFT plus a worker-thread delay it grows on underrun.
- **Output-processing config to copy:** DeepFilterNet's
  `deepfilter-stereo-sink.conf` — `capture.props = { media.class =
  Audio/Sink }`, `playback.props = { node.passive = true }`.
- **Multi-format plugin frameworks:** DPF (LADSPA, DSSI, LV2, VST2, VST3,
  CLAP); nih-plug (Rust; VST3 + CLAP only, VST3 bindings GPLv3); JUCE (VST,
  VST3, AU, LV2) — werman's noise-suppression-for-voice uses JUCE plus a
  hand-written LADSPA wrapper.

#### Windows

- **Equalizer APO hosts VST2 only** (`VSTPluginMain`, `kEffectMagic`) and its
  `GetLatency` never reports plugin delay **[source]**. A VST3/CLAP build
  does not give system-wide output processing through it; werman ships a
  VST2 build via the reverse-engineered FST headers for exactly this.
  Only the low-latency model is viable there.
- **Open-source virtual devices:** VirtualDrivers/Virtual-Audio-Driver (MIT,
  beta, test-signing), Scream (MS-PL, last release 2022), Microsoft SysVAD
  sample. Kernel drivers need Dev Portal signing since Windows 10 1607.
- Per-process loopback (`AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS`, build 20348+)
  captures without muting the original — confirms D3's point that loopback
  taps rather than inserts.

### 1.5 Browser companion: look ahead instead of running live

- **Look-ahead is real:** easysubs (<https://github.com/Nitrino/easysubs>,
  MIT, 302★) wraps `MediaSource.prototype.addSourceBuffer` and
  `SourceBuffer.prototype.appendBuffer` from a MAIN-world `document_start`
  script and copies audio segments "often a minute ahead of the playhead",
  decoding them with `OfflineAudioContext.decodeAudioData`. musicremover
  (§1.3) does the same on YouTube and raises its readahead to 60 s.
  DRM-protected segments don't decode.
- **Delaying video is also real:** Audio-Video-Delay
  (<https://github.com/cpb34/Audio-Video-Delay>, GPL-3.0) copies frames to
  WebGL via `requestVideoFrameCallback` and draws delayed frames over the
  hidden original.
- **Design:** separate buffered audio ahead of the playhead with a heavy
  model, play the result through Web Audio locked to `video.currentTime`,
  mute the element. Zero added latency on non-DRM video-on-demand (most of
  YouTube). Complements, does not replace, the system-wide path.

### 1.6 DSP and hybrid levers, ranked for this app's failure modes

#### 1. Rework mask post-processing (pumping, syllable loss, musical noise, the B4 bug)

**Read the scope before the recipe.** This is a **quality** lever — pumping,
`vocal-loss`, musical noise, a gain collapsing faster than a syllable — and it
is ranked first for those failure modes alone. It is *not* a route to keeping
sound effects, and "unblocks `attack_ms`/`release_ms`" below means the
mechanism is unblocked, not B4's original goal. Pure-DSP SFX add-back masks are
in ROADMAP §11's "do not re-explore" list at a measured ~8 % music-leakage
plateau, because drums are transient and noisy exactly like SFX; a monotonic
reshape of one scalar per bin preserves the model's own ranking and cannot
separate them. ROADMAP B4 is itself correctly scoped as a quality lever; only
the HANDOVER write-up conflated it with SFX. Keep the two apart.

- Smooth `|Wet|²` and `|Dry|²` **separately**, with the **same** α for both
  in a given bin and frame, per ERB band (32–48 bands, triangular
  interpolation back to bins; RNNoise uses 22):

  ```
  P_D ← α·P_D + (1−α)·|Dry|²
  P_W ← α·P_W + (1−α)·|Wet|²
  g   = clip( sqrt(P_W / (P_D + ε)), g_floor, 1 )
  ε   = max(abs floor ≈ −100 dBFS per bin, 1e-4 · mean_k P_D)
  ```

  Then apply γ and `floor_db` to `g`.
- **Why this fixes the B4 bug:** `P_W/P_D` is a Dry-energy-weighted average
  of the instantaneous ratios, so near-silent frames carry almost no weight
  and no stale value survives; new Dry energy dominates at once; clamping at
  1 makes amplification bursts impossible (de-musicing never needs gain > 1).
- Time constants: open fast (τ ≈ 5–10 ms), close slow (τ ≈ 80–150 ms plus a
  hold) so the gain cannot collapse inside a syllable; `α = exp(−hop/τ)`,
  chosen by direction but applied to both accumulators.
- Optional cepstral smoothing of the gain (Breithaupt, Gerkmann & Martin,
  ICASSP 2008, 16 ms hop): smoothing constants 0.5 for quefrency 0–2, 0.7
  for 3–19, 0.97 for ≥ 20; 0.2 on the pitch quefrency ±2; β = 0.96;
  G_min = −17 dB.
- If budget allows, delay audio 2–4 frames (20–40 ms) and smooth with a
  look-ahead window so the gain opens *before* onsets.
- Cost: < 0.2 ms/block **[estimate]**, 0–40 ms latency.
- Avoid DeepFilterNet's post-filter (`g' = g·sin(πg/2)`, β = 0.02): it
  sharpens attenuation, i.e. *more* vocal loss.

#### 2. A small causal separator trained on music

The only lever likely to move vocal/music separation past ~1 dB. See §1.2.

#### 3. Gate and route on voice presence (music-only interludes)

- Silero VAD (<https://github.com/snakers4/silero-vad>, MIT, ONNX, 16 kHz,
  ≥30 ms chunks, < 1 ms per chunk) on the **Wet** signal; when voice
  probability stays < ≈0.2 for > 300 ms, ramp to −30…−40 dB; reopen with a
  ≈5 ms attack (with lookahead if available).
- A slower tagger at ≈2 Hz picks a speech / singing / music-only profile:
  YAMNet (3.7M, 0.96 s window, Apache-2.0 — converts to ONNX, so ROADMAP
  §11's "TF-based" objection no longer holds), EfficientAT mn04_as (0.98M,
  MIT code, weights' license unstated), PANNs (CC BY 4.0 weights),
  inaSpeechSegmenter (MIT). Avoid CED (GPL-3.0).
- Caveat: Silero on singing is only validated at clip level (83/90 clear
  vocal clips, misses faint/choral/heavily processed vocals,
  arXiv 2609.20195).

#### 4. Replace the mid/side ratio with smoothed spatial statistics, as a soft prior

- Why the current filter gives ~1.8 dB **[estimate, derivation]**: its gain
  `(|M|²/(|M|²+|S|²))⁴` equals `(½(1 + ψ·cos φ))⁴` where
  `ψ = 2|L||R|/(|L|²+|R|²)` and φ is the inter-channel phase difference.
  Centre-panned music (kick, bass, snare, mono keys) passes at gain 1;
  decorrelated backing is cut on average to ≈ −11 dB but jumps 0↔1 frame to
  frame (musical noise); vocal reverb/doubling is punished by the exponent
  (≈ −10 dB at ψ·cos φ = 0.5), which is the extra vocal loss B1/B2 measured.
  A 512-point FFT at 48 kHz (93.75 Hz bins) is also too coarse for low-band
  stereo cues.
- Better: smooth Φ_LL, Φ_RR, Φ_LR (τ ≈ 20–40 ms, 2048-point FFT);
  coherence `|Φ_LR|/√(Φ_LL·Φ_RR)` separates direct from ambient (Avendano &
  Jot); panning-index window (Avendano, WASPAA 2003)
  `Θ = ν + (1−ν)·exp(−(Ψ−Ψ₀)²/2ξ)`, ξ ≈ 0.006. Feed it into the neural mask
  as a soft weight (exponent ≈ 0.5), not as a pre-filter.
- Limits: cannot remove centre-panned music; reverb counts as ambience;
  widened vocals need shifted PCA (He, Tan & Gan); hard-panned double
  tracks are spatially indistinguishable from guitars.

#### 5. Online REPET-SIM → Wiener post-filter

Rafii's defaults: 10 s buffer, ≤100 similar frames ≥1 s apart, ≈40 ms
window, 100 Hz cutoff; background `B = min(median of similar frames, X)`.
Use B as the music PSD in a decision-directed Wiener post-filter on Wet
(α_dd = 0.98, ξ_min = −25 dB). Store the residual `|Dry−Wet|` in the buffer
instead of the mix to keep repeated choruses out of the template
**[estimate]**. Its original evaluation lost on perceptual quality, so treat
as experimental. Reference code has no license (zafarrafii/REPET-Python) or
is archived (nussl, MIT, archived 2024-12).

#### 6. Pitch protection for the voice's harmonics (high-frequency vocal loss)

Track f0 on Wet (PESTO streaming — LGPL-3.0 code, weights' license unstated,
≈0.7 ms per inference **[vendor]**; SwiftF0 — MIT, ORT, but 176 ms
lookahead); raise the gain floor along a comb at h·f0 (width ≈ max(1 bin,
3 % of the harmonic)), weighted by confidence × VAD. RNNoise's pitch comb is
a shipped precedent. Monophonic trackers only — protect voiced frames only.

#### 7. A slow separator as an online teacher (highest effort)

Run Spleeter asynchronously at ≥1.1 s; learn a 20–40-atom accompaniment NMF
dictionary from its accompaniment output (online PLCA, Duan, Mysore &
Smaragdis 2012); solve activations per frame on the fast path and use the
mask as a prior. No published runtime system does this.

**Low payoff:** causal HPSS — consonants count as "percussive", so removing
the percussive part harms speech; useful only for drum leaks while no voice
is present.

### 1.7 Evaluation — making the numbers agree with ears

- Torcoli, Kastner & Herre (TASLP 2021, arXiv 2110.11438): the 2f-model beat
  PEASS, ViSQOLAudio and BSSEval against separation listening tests. No
  open implementation; PEAQ ports (gstpeaq, AQUA-Tk, GPL-3.0) supply its
  inputs.
- Jaffe & Burgoyne 2025 (arXiv 2507.06917): plain SDR tracked listeners best
  **for vocals**; embedding metrics (CLAP) did not.
- Sarkar et al. (ISMIR 2025): report error categories and distributions, not
  averages — consistent with this repo's named-failure tags.
- MAPSS (arXiv 2509.09212): separate leakage from target distortion.
- Practical: score vocal loss and music leakage separately on vocal and
  non-vocal segments (the harness already does), **report per mix ratio
  instead of averaging a100 with a200** (HANDOVER records that the average
  hid an audible a200 failure), and add a pumping index = 2–8 Hz modulation
  of the gain envelope on music-only segments **[estimate]**.

---

## Part 2 — Review of the current pipeline

### 2.1 What the pipeline is

```
apps → null sink "MusicAssassin" (set as default; reports no latency)
     → PortAudio duplex stream, 48 kHz stereo, 20 ms blocks, Python callback
     → queue (8 blocks) → worker thread:
         mid/side (optional) or mono downmix → soxr resample → ONNX model
         → resample back → 20 Hz–20 kHz band-limit → stereo rebuild (optional)
     → wet FIFO ← callback pairs it with a lockstep dry FIFO
     → dry/wet mix with 20 ms ramp → soft limiter (only if wet gain > 1)
     → real hardware sink
```

Done well and worth keeping: inference off the callback thread; warm-up
before a model goes live; ORT pinned to one thread with spinning off;
complete state reset on a model swap (`_swap_runtime`); stateful soxr
resamplers; DPDFNet state seeded from ONNX metadata (A5); DPDFNet STFT
scaling matches sherpa-onnx's reference (unnormalized forward DFT, 1/N
inverse — checked against `online-speech-denoiser-stft-impl.h`).

### 2.2 Bug — chunked processors cannot run in the live engine **[measured]** — fixed 2026-10-22

`AudioEngine._max_out = BLOCK * 8` (160 ms, `engine.py:178`), and `_work()`
discards everything older than that from `_out` (`engine.py:711-712`).
`SpleeterProcessor.feed()` returns a whole chunk at once (~1 s at the
default). So each second the worker keeps the newest 160 ms and drops the
rest; the callback then underruns and emits **dry** — the original audio,
music included.

A fake chunked processor (identity output, negated to tell wet from dry)
driven through the real `AudioEngine` with 20 ms callbacks, no devices:

| chunk | processed share of output (after 2 s warm-up) | `fallback_blocks` / 250 |
|---|---|---|
| 20 ms | 100.0 % | 1 |
| 100 ms | 100.0 % | 5 |
| 1000 ms | **16.0 %** | **218** |

The A1 sweep never saw this because `bench_quality.py` does not drive the
engine (§2.6). The lag estimator also only runs on the mono path
(`_collect_lag` is called from `_wet_mono_proc` alone), so a stereo
processor never gets a mix pad, and `_dry_out`'s cap (`_max_out + _mix_pad`)
cannot hold a chunk's worth of dry either.

**Fix:** size `_max_out` (and the dry caps) from the processor's
`latency_samples` converted to 48 kHz plus a margin, recomputed on swap;
measure (or declare, §2.7) lag for stereo processors too. Required before
any high-latency mode ships. StemgenRT emits per 128-sample hop and is not
affected.

**This is live, not latent [from code].** `available()` appends `spleeter`
whenever its two ONNX files and sherpa are present
(`processors/__init__.py:81-82`), and `create()` accepts any
`spleeter_<n>ms`, so a user with the models installed can select a
configuration that returns 16 % processed audio and 84 % dry. The processor
declares the right number — `latency_samples = chunk + context` ≈ 1.09 s
(`spleeter.py:111`) — and the engine ignores it. Configurations at or below
the 160 ms cap are safe (the 100 ms row processes 100 %); the unsuffixed
`spleeter` default is 1000 ms.

**Fixed in the working tree, 2026-10-23 — uncommitted.** `engine.py:205-224`
now sizes `_max_out` as `BLOCK * BASE_FIFO_BLOCKS + 2 * held`, with `held`
taken from the processor's own `latency_samples` rescaled to 48 kHz: the fix
prescribed above. `tests/test_engine_chunked.py` covers the case, is wired
into `run_tests.sh`'s hardware-free list, and **passes** (run 2026-10-23).
The stopgap an earlier draft of this note proposed — restricting the offered
chunk sizes — is therefore unnecessary and should not be built.

Reproduction script: written during the review to the session scratchpad as
`review/chunked_engine_sim.py`; it should become a test (see §2.6).

### 2.3 Bug — trimming wet breaks the dry/wet lockstep **[from code]** — fixed 2026-10-22

When `_work()` trims the head of `_out` (`engine.py:711-712`), the dry
samples those wet samples were paired with stay at the head of `_dry_out`.
From then until the next reset, every pairing is skewed by the trimmed
amount: an echo at any mix below 100 %, and `latency_ms` (which reads
`len(_dry_out)`) overstates. Rare with today's block-synchronous models (the
input queue caps at exactly 8 blocks = the `_out` cap), guaranteed with
§2.2. **Fix:** trim the paired dry samples in the same step, which needs
the two FIFOs handed off together.

### 2.4 Every failure path leaks the music

Underrun tail (`engine.py:618`), callback exception (`engine.py:550`,
`outdata[:] = indata`), startup (`engine.py:602`), and warm-up during a
model swap all emit the original audio. "Never silence" is right for a
volume booster and wrong for this product: leaking music is the worst
outcome, especially for listeners avoiding it on religious grounds. It is
also the "pulsing beat" the handover describes (dry/wet alternation on
underrun). **Suggestion:** a fail-closed mode — on underrun, fade to silence
or hold/crossfade the last good wet audio; on error, silence. Possibly the
default for a "hard mute" preset (§1.3 idea 6).

### 2.5 Video runs ahead of audio

See §1.4. The trap sink is created with no latency properties
(`backends/pipewire.py:204-211`) and the null-sink type cannot carry any.
Players compensate for nothing of ≈50 ms pipeline + ≈70 ms PortAudio
buffering (`device_latency_ms`, measured 34.8 + 34.8 ms at sounddevice's
default) + Bluetooth. Fix is architectural (graph-linked node reporting
`ProcessLatency`); the loopback hack needs PipeWire ≥ 1.4.

### 2.6 Two implementations of the chain

`tests/bench_quality.py::run_chain` re-implements `AudioEngine._work()` /
`_callback_body()` ("mirrors"), importing the DSP pieces but not the
engine. Every quality number therefore comes from the copy, and anything
that lives in the engine's FIFOs — §2.2, §2.3, the latency ratchet — is
invisible to it. **Fix:** add a plumbing tier that drives `AudioEngine`
offline by calling `_callback` synchronously with paced blocks (as the
reproduction script does), and at minimum assert processed share ≈ 100 %
and dry/wet alignment for every registered processor.

### 2.7 A fixed delay is measured live with a heuristic

Each model's lag is a constant of model + resampler chain (`dpdfnet_hr`
50 ms, `dtln` 8 ms, `gtcrn` 5.3 ms per `lag.py`), yet the engine finds it by
cross-correlation at every start. That is why `measuring…` and
`unmeasured` exist, why the stereo rebuild stays bypassed for the first
≈1.5 s of non-silent audio, and how a wrong measurement once produced the
doubling. Meanwhile `StreamProcessor.latency_samples` is documented as wrong
for all three models and worked around rather than corrected. **Fix:**
measure each model's lag once offline (the bench already has
`estimate_lag`), store it in the model card (ROADMAP A3), correct
`latency_samples`, and keep the live estimator as a verification check
that warns on disagreement.

### 2.8 The stereo rebuild's premise no longer holds

`audio/stereo.py` justifies reusing the dry phase with "the shipped enhancers
are all magnitude-mask models — phase untouched". HANDOVER ("The
permissiveness knob") later found `dpdfnet_hr` modifies phase (deep
filtering) and `dtln`'s second stage refines in the time domain — which is
why the first `shape_mask()` that resynthesised from the mask lost 4 dB
dSI-SDR at identity. So for the default model, the rebuild discards part of
what the model did. Separately, `MAX_GAIN = 8.0` lets a near-silent dry bin
be boosted by up to +18 dB — the same failure class as the B4 bug. **Fix:**
clamp the mask at 1.0 and derive it from smoothed powers (§1.6 item 1);
consider the correction form `shape_mask()` already uses (multiplicative
correction of wet) rather than resynthesis from dry.

**Correction, 2026-10-23 — clamping at 1.0 does not work, measured both
ways.** The clamp was tried (`MAX_GAIN = 1.0`, uncommitted) and the two
requirements turn out to be mutually exclusive under this formulation:

| `MAX_GAIN` | anti-phase amplification | image restoration |
|---|---|---|
| 8.0 | **fails** — out/in RMS 6.89, i.e. +16.8 dB | passes, −3.0 dB side retained |
| 1.0 | passes | **fails** — −235.9 dB, i.e. total collapse |

Deterministic, 3 runs of 3, and not the documented timing flake: −235.9 dB is
bit-for-bit the value the *bypassed* mono path reports in the same run, so the
rebuild is contributing nothing at all rather than contributing a narrowed
image.

**Why the cap cannot be the fix.** The mask formulation *requires* gain > 1 for
anti-phase content. With `L = c + s`, `R = c − s` and a small centre, the mono
downmix is ≈ `c` while L and R are at full level, so reconstructing them from a
mono reference needs a mask far above 1. Capping at 1 caps the output at
`|Dry_mono|` per bin — the centre alone — which is the collapse above. The cap
treats the symptom; the cause is the **denominator**: `|Dry_mono|` is the wrong
reference for content that does not survive the mono sum.

**Fix direction instead:** floor the denominator against the channel levels
rather than clamping the quotient — per bin, `max(|Dry_mono|, k·max(|L|, |R|))`
— or derive the mask from mid+side energy rather than the mono sum alone. An
anti-phase bin then yields a mask ≤ 1 *naturally*, because its reference is the
channel level and not the cancelled sum, and legitimate restoration survives.
A scalar clamp on the quotient can satisfy one requirement or the other; a sane
denominator can satisfy both. Untested — this is a design change to B3's core
formulation, on a path that ships off by default, so it wants a decision rather
than a quiet edit.

**Correction to the correction, 2026-10-24 — the clamp works; the collapse was
the timing flake.**

The A/B was re-run on an idle machine. Local copies of the package, identical
except for `stereo.py`, four runs each:

| `MAX_GAIN` | image restored | image restored, lagged model | anti-phase |
|---|---|---|---|
| 8.0 (HEAD) | −2.9 to −3.0 dB, 4/4 | −3.0 dB | out/in 6.89, fails 4/4 |
| 1.0 | −3.1, −6.0, −3.1, −3.2 dB, 4/4 | −3.2 dB | 1.00, passes 4/4 |

The whole suite also passes with `MAX_GAIN = 1.0`.

The −235.9 dB above is the value the test reports whenever the lag
measurement has not finished before the measured tail. That is a run where
the rebuild never engaged, not one where it collapsed the image. It fails
that way under CPU load: the 2026-10-23 runs happened while two corpus
sweeps were running. It also fails that way when run from the sftp-mounted
tree, at any `MAX_GAIN`; HEAD's 8.0 failed the same check under load on
2026-10-22. Run 2 here, at −6.0 dB, is the same effect partway: the rebuild
engaged late inside the window.

**Why the algebra above does not hold.** The mask is `|Wet| / |Dry_mono|`,
applied to L and R. Anti-phase content that the model leaves alone gives
`Wet ≈ Dry_mono`, so the mask is ≈ 1 and L and R pass through unchanged.
Reconstructing them never needs a gain above 1. A mask above 1 only arises
where wet *exceeds* the mono dry in a bin. That is the residual-over-
cancellation case the clamp exists to stop, and the band-limit's sub-dB
ripple. The latter is the measured 0.2 dB cost.

**The denominator idea is still worth having, for a different reason.**
Under the cap, a bin that cancels in mono passes the pair at unity. So
side-only music in those bins is kept, never suppressed. Referencing the
channel level instead would attenuate it. That bears on Q5, whether the
restored side channel brings music back. It is a separate improvement, not a
fix for the clamp.

### 2.9 Python in the real-time path

The PortAudio callback is Python: it allocates on every block
(`indata.copy()`, `np.concatenate` on `_dry_out`, `np.empty_like`,
`np.linspace`), takes `queue.Queue`'s lock and `self._lock`, and shares the
GIL with the worker and the Tk UI thread. About 70 ms of PortAudio buffering
is spent absorbing that jitter, and cutting it was tried and reverted
(2026-08-21). This is the core argument for ARCHITECTURE Phase 3 (native
node), which would also fix §2.5. Short of that: preallocated ring buffers
instead of concatenate/slice FIFOs reduce allocation churn.

### 2.10 Smaller issues

- **Mid/side** — unsmoothed per-bin ratio raised to the 4th power; see
  §1.6 item 4 for why it measures badly and what to replace it with.
- **Polling on the UI thread** — `_tick` runs every second on the Tk thread
  and spawns `wpctl`/`pw-dump` subprocesses, re-asserting the default sink
  whenever something takes it. WirePlumber smart filters (§1.4) remove the
  need; `pw-dump --monitor` would turn polling into events.
- **Band-limit filter** — a per-sample pure-Python biquad loop
  (0.155 ms/block **[measured]**, holding the GIL throughout). Its value is
  doubtful at 48 kHz (soxr already band-limits the 16 kHz models), and it
  sits *before* the stereo rebuild derives `|Wet|/|Dry|`, so its response
  leaks into the mask (HANDOVER notes boosted bins > 1 from it).
- **Soft limiter only engages when wet gain > 1** (`engine.py:634`); a dry
  volume above 1 is unprotected.
- Already tracked in ROADMAP: no input normalization (B10); the latency
  ratchet (dry FIFO grows on every underrun and never drains).

### 2.11 Suggested order

1. §2.2 + §2.3 + the §2.6 plumbing test — needed before StemgenRT or any
   separator goes live.
2. Wrap StemgenRT (vocals subgraph) as a processor and sweep it against
   `dpdfnet_hr` and `spleeter_4000ms`.
3. §1.6 item 1 (power-smoothed mask) — unblocks B4 and fixes §2.8's
   `MAX_GAIN`.
4. §2.4 fail-closed mode, and VAD gating (§1.6 item 3).
5. §2.7 offline lag in model cards (with A3).
6. Latency reporting (§2.5 / §1.4) — test player compensation hands-on
   first; it decides whether a high-latency separator mode is viable at all.

**Superseded 2026-10-23 by ROADMAP §9's "Current order".** Most of items 1–3
landed in the working tree on 2026-10-22/23, all of it uncommitted: the §2.2
and §2.3 fixes, per-processor lag measurement in a new `audio/lag.py`, the
§2.6 plumbing test, the StemgenRT wrap (`processors/stemgen.py`, benched here
at RTF 0.094), and §1.6.1's power-smoothed mask as `audio/maskshape.py` —
which also corrects the recipe, using an absolute accumulator floor only,
because the relative `1e-4 · mean_k P_D` term biases every quiet bin's gain
downward. ROADMAP §9 reconciles against the tree and carries what is actually
left: landing and verifying it, the spoken-dialogue gate on StemgenRT (still
untested), the corpus and sparse-material sweeps, the non-commercial
training-data licence (ROADMAP §10 Q6), and item 6's player-compensation
experiment — which it moves early, because it is a decision input rather than
an implementation.

**Status, 2026-10-22.** Items 1–3 are built; uncommitted on
`feat/separator-spike`.

- **1 — done.** FIFO caps now scale with the installed processor's declared
  latency (`AudioEngine._apply_caps`). Either side's trim now drops the
  matching samples from the other side, so the pairing stays lockstep.
  `tests/test_engine_chunked.py` drives the real `AudioEngine` one block at a
  time, without the §2.6 copy. It checks four things: the processed share at
  20/100/1000/4000 ms chunks, 50/50 cancellation of a negating processor, a
  stall that hits both caps, and the caps themselves. With the bookkeeping
  mutated out, the cancellation test fails (residual 7e-2).
- **2 — wrapped, sweep running.** The processor is `processors/stemgen.py`;
  `scripts/fetch_stemgen.py` cuts out the vocals subgraph and checks it is
  bit-identical to the full graph before installing it. The model card is
  `models/stemgen_rt_vocals.json`. On one clip: SDR 9.49 dB against the
  untouched mix's 2.10 dB, lag 0, 1.88 ms per 20 ms block.
- **3 — done for the mono and wants_stereo paths.** The shaper is
  `audio/maskshape.py` and runs in the engine; `tests/test_mask_shaper.py`
  covers it. The stereo rebuild's `MAX_GAIN` (§2.8) is now 1.0 (2026-10-23).
  At 8 it amplified anti-phase content by +17 dB; a test in
  `tests/test_stereo_rebuild.py` now covers that case.
- **§2.6 — partly closed.** The bench's `shape_mask()` now streams through
  the engine's own `MaskShaper`. Moving it also found that its alignment had
  been inverted: dry was advanced where it should have been delayed, so the
  mask was measured 2× the model's lag away — 100 ms on `dpdfnet_hr`. The
  earlier B4 sweep numbers in HANDOVER were measured that way.
