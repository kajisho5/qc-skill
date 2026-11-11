# Roadmap — qc-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **検証 (Verification)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v1.0.0 (cut from a dependabot bump alone; the agent's gate accepts only 0.1.x / 0.2.x, see video-production-agent step 11), Python 3.9+ stdlib. contract, doctor, run (inspect/check/validate; video/audio/subtitle/delivery/delivery_package; 53 checks after steps 5-7). 22 test modules, CI on 3 OSes x 2 Pythons. Already an OS registry fixture.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 規格判定(QC) | done | VMAF/SSIM/PSNR added (step 3); its own receipt still open (docs/qc-evolution-gap-analysis.md) |

## Wiring

Agent wires qc/check and qc/inspect (ADR-032); provides[] publishes qc/check as tool_id, qc/check + qc/inspect as tool_ids and qc/run as an alias (step 2, ADR-022). delivery_package used by the agent's QC gate (step 4, video-production-agent#82). The agent pins this contract at 0.2.6. Calls ffprobe/ffmpeg directly.

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
- [ ] **8. Frozen time (low-motion spans)** — spans where the picture keeps changing a little but nothing moves: a stalled animation, a held title, a camera left on an empty stage. Motion energy per window (frame difference after a small blur, scene-change frames excluded) below a threshold for longer than `max_frozen_time_sec` → `VIDEO_FROZEN_TIME`. The ranges the caller expects to be still (planned freezes, image inserts, slides) are passed as `expected_still` and skipped. It sits next to the exact-freeze check that already exists (`video.freeze_segments` from freezedetect, `VIDEO_FREEZE_EXCEEDED`), which misses noisy or slowly drifting stills. ffmpeg-skill `check --content` stays the engine's whole-file preflight. 参考：echris6/motion-video-kit（MIT）
  - Done when: a 4 s held title with grain FAILs and is missed by the exact-freeze check; a slow pan PASSes; an expected still is skipped
- [ ] **9. Render critique report** — one report for the agent's post-render loop (video-production-agent step 15): every finding with its time range, the check id, the measured value, the threshold and a stable finding id, ordered by time and grouped by cut boundary when an `expected_cuts` list is given (step 6). Findings come from the checks qc already has (black, freeze, frozen time, flash, cut mismatch, loudness, text legibility); qc judges and reports, it never fixes, re-renders or decides (ADR-007 of Velisa). A second run with the previous report marks each finding fixed, still there or new. 参考：echris6/motion-video-kit（MIT）
  - Needs: step 8 for frozen time
  - Done when: two runs on a render and its fixed version report one finding as fixed and none as new; the report validates against its schema

## Boundaries

ffmpeg-skill `check` (書き出し規格の検査) is the engine-side preflight; qc judges against the spec. Same measure.audio.* ids as media-analysis.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (3.x, 42 tools, contract_version 2.0; treated as complete, proposals go to Velisa's ROADMAP). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
