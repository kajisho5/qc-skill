# Roadmap — qc-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **検証 (Verification)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v1.2.0, Python 3.9+ stdlib. contract, doctor, run (inspect/check/validate; video/audio/subtitle/delivery/delivery_package; 65 checks after step 9). 30 test modules, CI on 3 OSes x 2 Pythons. Already an OS registry fixture.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 規格判定(QC) | done | VMAF/SSIM/PSNR added (step 3); its own receipt still open (docs/qc-evolution-gap-analysis.md) |

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
- [ ] **10. Loop-seam check for audio** — handed over by sound-design-skill (step 7, its ADR-011): for a file declared loopable, the seam end → start (and any internal seams) measured for a click, a level step and a spectral step; every threshold a rule field with no default
  - Done when: a seamless loop and a bed repeated 10 times PASS, a naive cut FAIL with the click, each threshold's edge exact, an unreadable or too-short file UNKNOWN

## Boundaries

ffmpeg-skill `check` (書き出し規格の検査) is the engine-side preflight; qc judges against the spec. Same measure.audio.* ids as media-analysis.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
