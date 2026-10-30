# Roadmap — qc-skill

Part of the [AI Video Production OS](https://github.com/kajisho5/AI-video-production-OS/blob/main/docs/ecosystem/ROADMAP.md) ecosystem: department **検証 (Verification)**, status `active`.
Written from a read-only audit of this repository on 2026-10-30. Each step below is meant to be one pull request; tick it off by linking the PR.

## Where it stands

v0.1.3, Python 3.9+ stdlib. contract, doctor, run (inspect/check/validate; video/audio/subtitle/delivery/delivery_package; 50 checks). 22 test modules, CI on 3 OSes x 2 Pythons. Already an OS registry fixture.

## What this repository owns

`ecosystem.json` gives each job to exactly one repository. Work outside this list belongs to its owner and is handed over, not re-implemented.

| Owned job | Status | Note |
|---|---|---|
| 規格判定(QC) | done | VMAF/SSIM/PSNR and its own receipt still open (docs/qc-evolution-gap-analysis.md) |

## Wiring

Agent wires qc/check and qc/inspect (ADR-032); provides[] publishes only qc/run (tool-id mismatch). delivery_package never used. Calls ffprobe/ffmpeg directly.

## Steps

- [ ] **1. Enforce request.timeout** — kill the process group, TIMEOUT error
  - Done when: 0.01 s timeout test
- [ ] **2. Per-operation tool_ids in provides** — qc/check, qc/inspect (keep qc/run alias)
  - Done when: no tool-id drift in agent diagnostics
- [ ] **3. Reference metrics** — opt-in VMAF/SSIM/PSNR, UNKNOWN without libvmaf
  - Done when: identical input PASS, degraded FAIL
- [ ] **4. (agent side) delivery_package gate** — video + subtitles + thumbnail checked together
  - Done when: missing sidecar = FAIL, not promoted

## Boundaries

ffmpeg-skill `check` (書き出し規格の検査) is the engine-side preflight; qc judges against the spec. Same measure.audio.* ids as media-analysis.

---
Engine for every media operation: [ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) (42 tools, treated as complete). Orchestrator: [video-production-agent](https://github.com/kajisho5/video-production-agent).
