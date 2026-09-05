# Capability matrix and gap analysis (Phase E: multi-skill pipeline QC)

This document is the evidence base behind ADR-009 and ADR-010
(`docs/decisions.md`). It is descriptive, not a contract: nothing here is
implemented until it appears in `qc contract --json` and
`docs/checks.md`. It exists so later phases don't re-derive "why are we
building this and not that" from scratch, and so a reviewer can check the
reasoning against the same sources this repo used.

## Method

Two inputs, both evidence-based rather than assumed:

1. **Ecosystem read** of all repos qc-skill sits next to
   (`video-production-agent`, `ffmpeg-skill`, `subtitle-skill`,
   `color-grading-skill`, `motion-graphics-skill`, `audio-production-skill`,
   `thumbnail-skill`, `video-editing-skill`, `media-analysis-skill`) - what
   each already validates, at what stage, and what it explicitly does not
   claim.
2. **Competitor read** of the media-QC landscape as of 2026: QCTools,
   MediaConch, VMAF (libvmaf/ffmpeg), Netflix `rendercheck`, plus
   agent/pipeline-native tools: `uploadcheck-mcp` and Kinocut (Video
   Receipts).

## Capability matrix

Columns are the 12 dimensions the evolution plan asked to compare against.
"—" means not applicable/not claimed by that tool; this is not a criticism,
most of these tools were never trying to do the other 11 things.

| Dimension | QCTools | MediaConch | VMAF | rendercheck | uploadcheck-mcp | Kinocut | **qc-skill (current, PR #1)** | **qc-skill (target)** |
|---|---|---|---|---|---|---|---|---|
| Measurement (typed, tool-attributed) | yes (interactive graphs) | yes (MediaInfo-based) | yes (single metric) | yes | yes | yes | **yes** (`QCMeasurement`, ffprobe/ffmpeg-attributed) | unchanged |
| Rule (typed, caller-supplied, no baked policy) | no (visual inspection) | yes (MediaConformance policy XML) | no (raw score only) | partial (hardcoded thresholds) | yes (config-driven gates) | yes (workflow-defined gates) | **yes** (`VideoRule`/`AudioRule`/`SubtitleRule`/`DeliveryRule`) | unchanged |
| Finding (typed, evidence-linked, not just pass/fail) | no | partial | no | partial (log lines) | yes | yes | **yes** (`QCFinding` w/ `measurement_ids`) | unchanged |
| Timestamp-level evidence | yes (frame-accurate plots) | no | no (whole-asset score) | partial | no | partial | **yes** (black/freeze segments, silence segments) | strengthen (Phase 4) |
| Delivery gate (N named artifacts as ONE unit) | no | no | no | no | partial (single-file gate) | yes (package-level) | **partial** (`delivery` = 1 primary + optional companions, not N-artifact) | **Phase 1** |
| Cross-artifact validation (typed relationship rules across artifacts, not string compare) | no | no | no | no | no | partial | **no** | **Phase 2** |
| Timeline-awareness (post-edit delivery timing vs. source cue timing) | no | no | no | no | no | **yes** (workflow-engine aware) | **no** | **Phase 3** |
| Deterministic identity (same input+rules+tool-versions -> same result id) | no | no | no | no | unclear (not published) | yes (hash-based receipts) | **yes** (`provenance.identity`, ADR excludes timestamps/paths/request_id) | unchanged |
| Agent/orchestrator integration (native tool contract, not a CLI wrapper) | no | no | no | no | yes (MCP-native) | yes | **yes** (already integrated into video-production-agent, ADR-032, zero contract drift) | strengthen (Phase 6 receipt) |
| Remediation / auto-fix | no | no | no | no | no | no | **no (by design, ADR-001)** | **stays no, permanently** |
| Multi-tool evidence chaining (QC evidence spans MULTIPLE upstream skills' outputs, not one tool's own steps) | no | no | no | no | no | no | **no** | **Phase 1-2, and the eventual Evidence Graph** |
| Full audit trail ("why did this pass", tool versions + hashes + rules) | no | no | no | no | partial | yes (Video Receipts) | **yes** (provenance block) but not yet a standalone receipt object | **Phase 6** |

## What this rules out as a differentiator

"Agent-native QC" alone is **not** a gap - `uploadcheck-mcp` and Kinocut
already ship that. Copying either (or VMAF's scoring model, or
MediaConch's policy-XML approach) verbatim would not close a real gap and
is explicitly out of scope (evolution-plan Step 1: "copying competitors is
forbidden").

## Confirmed genuine gaps (this is what Phases 1-3 exist to close)

1. **Cross-artifact validation across a multi-skill pipeline's
   heterogeneous outputs.** Every tool surveyed either checks one file, or
   (Kinocut) checks a package it itself produced. None of them validate
   *"does this video's audio duration, this SRT's last cue, and this
   thumbnail's aspect ratio agree with each other"* when those three files
   were produced by three independent tools (video-editing-skill,
   subtitle-skill, thumbnail-skill) that never talk to each other.
2. **Genuine timeline-awareness of edit history**, not just source-file
   awareness. Ecosystem read confirmed: no single reusable "trim/concat/
   speed source-timestamp -> delivery-timestamp" object exists anywhere in
   this ecosystem today. `video_agent/temporal/timeline.py`'s
   `TimelineMap` is dormant and multi-camera-sync-only (unrelated to edit
   history); the real general model is `video-editing-skill`'s own
   `Segment`/`Clip`/`build_timelines()`, captured by the agent's adapter
   but never persisted or used downstream; and `agent/subtitles.py` has
   its own narrow, subtitle-cue-only reimplementation of the same idea.
   Nothing today can tell you whether a delivery's subtitle cues were
   correctly remapped after a trim.
3. **Deterministic evidence chained across multiple tools' outputs**, not
   just one tool's own steps. qc-skill's own `provenance.identity` is
   already deterministic per-request; the gap is upstream of that -
   nothing ties *this* QC verdict to *which* upstream Operation/Skill
   produced each artifact it measured.
4. **An orchestrator-facing evidence graph** tying a QC verdict into the
   agent's Decision loop as one auditable object spanning every skill's
   output - Finding -> Check -> Measurement -> Artifact -> Operation ->
   Skill -> ProductionPlan. Confirmed nothing surveyed (including
   Kinocut's per-package receipt) does this across a *multi-skill*
   pipeline; qc-skill's existing provenance block is the right foundation
   but stops at a single request's own inputs.

## Ecosystem constraints Phase 1+ must respect (confirmed, not assumed)

- qc-skill is **already** integrated into `video-production-agent`
  (ADR-032): `tools/qc/adapter.py` pins a `contract_0.1.0.json` snapshot
  and gates compatibility on a `"0.1."` version-prefix check. Any contract
  change should stay additive within the `0.1.x` line (as the `rules`/
  `findings` schema additions already did) rather than force a `0.2.0`
  renegotiation, unless a change is unavoidable and justified.
- **No "delivery package" (N named artifacts validated as one unit)
  concept exists anywhere in the ecosystem.** qc-skill's own single-video
  + optional-subtitle-companion `delivery` kind is the closest (narrow)
  precedent. There is no naming collision to avoid, but also no existing
  convention to copy - Phase 1 has to name and shape this itself. See
  ADR-010 for the resulting decision (new `kind`, not an extension of
  `delivery`).
- qc-skill **must never import `video_production_agent`/`ProductionPlan`
  types directly** - the existing adapter pattern
  (`tools/skill_process.py` + per-skill `contract_X.Y.Z.json` pin) is how
  every skill in this ecosystem stays decoupled from the agent, and Phase 1
  must produce a typed boundary object on the qc-skill side of that same
  seam, not reach across it.
- Every execution skill surveyed (`color-grading-skill`,
  `motion-graphics-skill`, `thumbnail-skill`, `video-editing-skill`,
  `subtitle-skill`) does its own narrow, creation-time self-verification
  ("did my own operation do what it claimed"). None of them claims
  delivery-gate or cross-artifact responsibility - there is no duplicate
  work to avoid stepping on, and no existing owner to defer to.

## Mapping to phases

| Gap | Phase |
|---|---|
| Delivery gate (N named artifacts, one unit) | Phase 1 - `feature/delivery-gate-foundation` |
| Cross-artifact relationship rules | Phase 2 - `feature/cross-artifact-qc` |
| Timeline-aware delivery-timing validation | Phase 3 - `feature/timeline-integrity-qc` |
| Additional timestamped visual-defect evidence | Phase 4 - `feature/visual-defect-qc` |
| Reference-based perceptual metrics (VMAF/SSIM/PSNR) | Phase 5 - `feature/perceptual-quality-qc` |
| Full multi-tool audit trail (Production Receipt) | Phase 6 - `feature/production-receipt` |
| Full orchestrator-facing Evidence Graph | Not scheduled - explicit future goal, not built until Phases 1-3 exist to hang it off of |
