# Roadmap — qc-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **検証 (Verification)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v0.1.3, Python 3.9+ stdlib. contract, doctor, run (inspect/check/validate; video/audio/subtitle/delivery/delivery_package; 53 checks after steps 5-7). 22 test modules, CI on 3 OSes x 2 Pythons. Already an OS registry fixture.

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

## Boundaries

ffmpeg-skill `check` (書き出し規格の検査) is the engine-side preflight; qc judges against the spec. Same measure.audio.* ids as media-analysis.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
