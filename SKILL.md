---
name: qc-skill
description: Deterministic media quality control / validation. Measures and checks video, audio, subtitle, and delivery artifacts against caller-supplied rules and returns structured PASS/WARN/FAIL/UNKNOWN results with evidence. Use when you need to know facts about a media file (resolution, fps, codec, loudness, silence, black/freeze frames, subtitle timing/coverage) or whether it meets an explicit spec (expected resolution/fps/codec, loudness target+tolerance, subtitle sync tolerance). Do NOT use this skill to decide whether media may ship, to edit/re-render/repair media, to generate or rewrite subtitles, or to transcribe/understand content semantically - those belong to video-production-agent (decisions), audio-production-skill / video-editing-skill (execution), subtitle-skill (generation), and transcription-skill (ASR).
---

# qc-skill

## Commands

```bash
qc contract --json
qc doctor --json
qc run <request.json | -> --json [--workspace DIR] [--allowed-input-root DIR]...
```

## Request parameters

| Field | Type | Meaning |
|---|---|---|
| `operation` | `inspect` \| `check` \| `validate` | measure only, or measure+evaluate rules |
| `kind` | `video` \| `audio` \| `subtitle` \| `delivery` | what `input` is |
| `input` | string | path to the primary artifact |
| `subtitle` | string, optional | companion subtitle path (`kind: delivery` only) |
| `reference_video` | string, optional | companion video path, for duration comparison (`kind: subtitle` only) |
| `parameters` | object, optional | detection sensitivity overrides (e.g. `silence_threshold_db`) |
| `rules` | object, optional | typed expectations: `{video, audio, subtitle, delivery}` (see `src/qc_skill/rules.py`) |
| `cache_policy` | `use` \| `bypass` \| `only` | default `use` |
| `timeout` | number, optional | seconds |

Never send `command`, `argv`, `args`, `shell`, `cmd`, `exec`, `executable`,
`filter`, `filter_complex`, or `env` - the request schema rejects all of
them outright, at any nesting depth.

## Example

```json
{
  "operation": "check",
  "kind": "audio",
  "input": "/workspace/mix.wav",
  "rules": {"audio": {"integrated_loudness_target_lufs": -16, "integrated_loudness_tolerance_lu": 1.0, "max_true_peak_dbfs": -1.0}}
}
```

Response: `{"status": "completed", "report": {"overall_status": "PASS"|"WARN"|"FAIL"|"UNKNOWN", "checks": [...], "measurements": [...], "findings": [...]}, "provenance": {...}}`.

## Workflow hints for an agent

1. Call `doctor --json` once per environment to confirm ffmpeg/ffprobe and
   the filters you need (`blackdetect`, `freezedetect`, `ebur128`,
   `astats`, `silencedetect`) are `AVAILABLE`, not just installed-in-theory.
2. Use `inspect` first when you don't yet know what to expect (e.g. before
   you have a delivery spec) - it returns pure measurements you can reason
   over yourself.
3. Use `check`/`validate` with explicit `rules` once you know the target
   spec. Never assume a default target (loudness, resolution, fps) - if
   the caller doesn't supply one, the corresponding check is simply not
   run, and `qc-skill` will not guess.
4. Treat `report.overall_status: "FAIL"` as **information**, not failure of
   your own operation - `status: "completed"` means the QC run itself
   succeeded. React to `findings[].code` to decide what to do next (that
   decision is yours, not qc-skill's).
5. A response `status: "failed"` (with an `error.code`) means the QC run
   itself could not complete - handle it like any other tool error.

## Boundaries

qc-skill never: makes a publish/block/re-render decision, edits or
re-encodes media, generates or rewrites subtitles, transcribes audio,
performs semantic/scene understanding, executes a caller-supplied filter
graph or shell command, or fabricates a measurement it could not actually
take (an unmeasurable value is `null` with a note, or the check is
`UNKNOWN` with a `reason` - never a guess).
