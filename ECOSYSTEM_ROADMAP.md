# Roadmap — qc-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **検証 (Verification)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v1.3.0, Python 3.9+ stdlib. contract, doctor, run (inspect/check/validate; video/audio/subtitle/delivery/delivery_package; 68 checks after step 10). 31 test modules, CI on 3 OSes x 2 Pythons. Already an OS registry fixture.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 規格判定(QC) | done | VMAF/SSIM/PSNR added (step 3); its own receipt still open (docs/qc-evolution-gap-analysis.md). Judgement against the plan or spec; the split with ffmpeg-skill `check` is settled by the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O13), see Boundaries |

## Wiring

Agent wires qc/check and qc/inspect (ADR-032); provides[] publishes qc/check as tool_id, qc/check + qc/inspect as tool_ids and qc/run as an alias (step 2, ADR-022). delivery_package never used. Calls ffprobe/ffmpeg directly.

## Steps

- [x] **1. Enforce request.timeout** — kill the process group, TIMEOUT error (exit 15, retryable; ADR-019) ([#21](https://github.com/kajisho5/qc-skill/pull/21))
  - Done when: 0.01 s timeout test
- [x] **2. Per-operation tool_ids in provides** — qc/check, qc/inspect (keep qc/run alias; contract `tools`, ADR-022) ([#25](https://github.com/kajisho5/qc-skill/pull/25))
  - Done when: no tool-id drift in agent diagnostics
- [x] **3. Reference metrics** — opt-in VMAF/SSIM/PSNR, UNKNOWN without libvmaf (`reference_metrics` + `reference_video`; size scaled, fps/length mismatch refused; ADR-021) ([#23](https://github.com/kajisho5/qc-skill/pull/23))
  - Done when: identical input PASS, degraded FAIL
- [x] **4. (agent side) delivery_package gate** — video + subtitles + thumbnail checked together (video-production-agent [#82](https://github.com/kajisho5/video-production-agent/pull/82), ADR-065)
  - Done when: missing sidecar = FAIL, not promoted
- [x] **5. Photosensitive flash check (ITU-R BT.1702)** — opt-in `check_photosensitivity`, `max_flashes_per_second` (default 3) → `VIDEO_FLASH_RISK`; red flash UNKNOWN (ADR-016)
  - Done when: 10 Hz full-screen flash FAIL, 2 Hz PASS, small-area flash PASS
- [x] **6. Planned-cut accuracy** — `expected_cuts` vs. scdet cut times, `max_cut_error_frames`/`allow_extra_cuts` → `VIDEO_CUT_MISMATCH` (ADR-017)
  - Done when: exact list PASS, 2-frame error / missing cut FAIL; agent can verify a render against its IR cut list
- [x] **7. Text legibility** — `text_regions` contrast (WCAG, default 4.5) and per-role ink height at 1080 → `TEXT_ILLEGIBLE`, no OCR (ADR-018)
  - Done when: low-contrast and undersized regions FAIL, box outside frame UNKNOWN
- [x] **8. Subtitle vs. speech** — opt-in `transcript` + `word_boundary_tolerance_sec` / `max_cue_lead_sec` / `max_uncovered_speech_sec` / `forbidden_line_start_pos|phrases`, `max_total_chars`, `min_cue_chars` (WARN), opt-in `preset: "ja"` (ADR-023)
  - Done when: each check PASS/FAIL/UNKNOWN with times in evidence; transcripts without `index`/`pos` still read
- [x] **9. Stinger cut-point check** — handed over by video-editing-skill (step 13, its ADR-032) and hyperframes-skill (step 15): opt-in `VideoRule.stinger {cut_point, cover}` from stinger.json → `video.stinger_opaque_at_cut_point` (every pixel's alpha >= 253/255 at the cut frame, video-editing-skill's rule; `VIDEO_STINGER_NOT_OPAQUE`) and `video.stinger_cut_point_in_cover`; with `file` + `cut_at` + `max_cut_frame_difference` on a delivered video, `video.stinger_cut_frame_matches` (`VIDEO_STINGER_CUT_FRAME_MISMATCH`); no alpha / unreadable frame UNKNOWN (ADR-024) ([#32](https://github.com/kajisho5/qc-skill/pull/32))
  - Done when: a stinger's cut frame PASS, a partly covered frame FAIL with the pixels below, 253 PASS / 252 FAIL, no alpha UNKNOWN; a delivered edit with the stinger on the cut PASS, one frame late FAIL
- [x] **10. Loop-seam check for audio** — handed over by sound-design-skill (step 7, its ADR-011): opt-in `AudioRule.loop_seam {include_wrap, seams, window_sec}` measures the wrap end → start and listed internal seams from decoded samples → `audio.loop_seam_click_within_limit` (`max_click_ratio`: second difference at the seam over its 99th percentile around it; `AUDIO_LOOP_SEAM_CLICK`), `audio.loop_seam_level_within_limit` (`max_level_diff_db`, RMS of `window_sec` each side; `AUDIO_LOOP_SEAM_LEVEL_MISMATCH`), `audio.loop_seam_spectrum_within_limit` (`max_band_diff_db`, octave-band shape; `AUDIO_LOOP_SEAM_SPECTRUM_MISMATCH`); no limit has a default (ADR-025) ([#33](https://github.com/kajisho5/qc-skill/pull/33))
  - Done when: a seamless loop and a bed repeated 10 times PASS, a naive cut FAIL with the click, each threshold's edge exact, an unreadable or too-short file UNKNOWN
- [ ] **11. Loop-seam check for video** — the audio check of step 10 has no picture counterpart, and the title, stinger and loop material that hyperframes-skill now owns ([Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156), O4) is checked on its seam only by the renderer itself. Opt-in `VideoRule.loop_seam {window_frames}`: the difference between the loop's last and first decoded frames, against the frame-to-frame differences inside the loop around the seam, → `video.loop_seam_within_limit` (`max_seam_ratio`; `VIDEO_LOOP_SEAM_JUMP`, with the frame pair and the measured values in evidence). Works on any loop file (hyperframes-skill `--segments` loop, ffmpeg-skill `loop --crossfade`, remotion-ffmpeg-skill `--loop`); with the material's `title.json` (the Velisa spec for title metadata) the loop segment is read from it. No limit has a default, as in step 10
  - Why: a loop that jumps once per repeat is visible on screen for as long as the title holds（なぜ要るか：ループ素材の継ぎ目の飛びを、画で判定できる）
  - Done when: a seamless hyperframes-skill loop and a slow pan cut on a whole period PASS; the same loop cut one frame short FAILs with the seam frames; an unreadable or one-frame file is UNKNOWN
- [ ] **12. Alpha-edge check for title and stinger material** — on material with alpha, check that the edges composite cleanly in the alpha mode it declares (`straight` or `premultiplied`, from `title.json` / `stinger.json` or the rule): for premultiplied, no colour channel above its alpha; for both, the material composited over black and over white at the declared mode shows no fringe (a halo of dark or light pixels) beyond a stated width where 0 < alpha < 255. → `video.alpha_mode_consistent` (`VIDEO_ALPHA_MODE_MISMATCH`) and `video.alpha_edge_clean` (`max_fringe_px`; `VIDEO_ALPHA_FRINGE`), sampled frames listed. No alpha is UNKNOWN. [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O4, O1)
  - Why: a lower third or stinger declared straight but delivered premultiplied, or the reverse, shows a dark or bright outline in every edit it is placed in（なぜ要るか：アルファの宣言違いによる縁の黒ずみ・白浮きを納品前に見つける）
  - Done when: a straight fixture declared straight PASSes; the same file declared premultiplied FAILs the mode check; a premultiplied file composited as straight FAILs the fringe check with its width; a file with no alpha is UNKNOWN
- [ ] **13. Key / fill match** — for a key / fill pair (ffmpeg-skill `export --preset key-fill`, or any two files the caller names): same size, frame rate and frame count, the same timing, and the key equal to the fill's alpha where the fill carries one (or, for a fill without alpha, every fill pixel outside the key black), frame by frame within a tolerance → `video.key_fill_match` (`VIDEO_KEY_FILL_MISMATCH`, with the first differing frame and the share of pixels off). [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O4)
  - Why: a key one frame late or a fill that leaks outside the key shows as a flash or an outline on a broadcast switcher（なぜ要るか：キーとフィルのずれを、スイッチャーに入れる前に見つける）
  - Done when: a pair made by ffmpeg-skill `key-fill` PASSes; the key shifted by one frame FAILs at that frame; a fill with a pixel lit outside the key FAILs; a size or frame-count mismatch FAILs before any pixel is read

## Boundaries

The split with ffmpeg-skill is settled by the [Velisa ownership decisions 2026-11-12](https://github.com/kajisho5/Velisa/pull/156) (O13), no wiring change: ffmpeg-skill `check` is the engine's preflight (fixed platform tables and thresholds, with fix commands; its owned job renamed `書き出し前の規格点検`), and qc judges against the plan or spec (rules from the caller, UNKNOWN when it cannot tell). media-analysis-skill reports facts without verdicts (its `integrity` aside). Same measure.audio.* ids as media-analysis. The checks on title, stinger and loop material (steps 9-13) judge what hyperframes-skill renders, motion-graphics-skill places and video-editing-skill cuts; qc never fixes or re-renders.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
