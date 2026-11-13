---
name: qc-skill
description: Deterministic media quality control / validation. Measures and checks video, audio, subtitle, and delivery artifacts - a single file, or (kind=delivery_package) N named artifacts (video + subtitle + thumbnail + metadata, ...) validated together as one delivery - against caller-supplied rules, and returns structured PASS/WARN/FAIL/UNKNOWN results with evidence. Use when you need to know facts about a media file (resolution, fps, codec, loudness, silence, black/freeze frames, subtitle timing/coverage) or whether it meets an explicit spec (expected resolution/fps/codec, loudness target+tolerance, subtitle sync tolerance, required companion artifacts). Do NOT use this skill to decide whether media may ship, to edit/re-render/repair media, to generate or rewrite subtitles, or to transcribe/understand content semantically - those belong to video-production-agent (decisions), audio-production-skill / video-editing-skill (execution), subtitle-skill (generation), and transcription-skill (ASR).
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
| `kind` | `video` \| `audio` \| `subtitle` \| `delivery` \| `delivery_package` | what `input` (or `artifacts`) is |
| `input` | string | path to the primary artifact (not accepted for `kind: delivery_package`) |
| `subtitle` | string, optional | companion subtitle path (`kind: delivery` only) |
| `reference_video` | string, optional | companion video path, for duration comparison (`kind: subtitle` only) |
| `artifacts` | array, optional | `[{artifact_id, artifact_type, path}, ...]` - N named artifacts validated together as one delivery (`kind: delivery_package` only, required and non-empty for it) |
| `parameters` | object, optional | detection sensitivity overrides (e.g. `silence_threshold_db`) |
| `rules` | object, optional | typed expectations: `{video, audio, subtitle, delivery, delivery_package}` (see `src/qc_skill/rules.py`) |
| `cache_policy` | `use` \| `bypass` \| `only` | default `use` |
| `timeout` | number, optional | seconds of wall clock for the whole request; when it runs out every running ffprobe/ffmpeg is killed with its process group and the call fails with `TIMEOUT` (exit 15, retryable) - ADR-019 |

Never send `command`, `argv`, `args`, `shell`, `cmd`, `exec`, `executable`,
`filter`, `filter_complex`, or `env` - the request schema rejects all of
them outright, at any nesting depth.
Numbers must be finite: NaN or Infinity anywhere in the request is
`INVALID_REQUEST`.

For `kind: "delivery_package"`, `rules.delivery_package.cross_artifact`
adds typed relationship checks across the named artifacts:
`duration_consistency` (durations must agree within a tolerance) and
`dependencies` (if artifact A is present, artifact B must be too) - never
a string comparison or semantic/LLM judgment. See `docs/checks.md`.

`rules.subtitle.timeline_integrity` (also reachable via `delivery`'s and
`delivery_package`'s nested `subtitle` sub-rule) checks whether a
subtitle's delivery-timeline cue timing still matches a caller-supplied
source-to-delivery `timeline` mapping after a trim/concat/speed edit -
qc-skill never constructs that mapping itself, only compares against
one the caller supplies. See `docs/checks.md`.

`VideoRule.max_single_luminance_excursion_sec`/
`max_total_luminance_excursion_sec` check for video signal outside the
legal 8-bit range (default 16-235) via ffmpeg's `signalstats` (a literal
per-frame pixel readout, not a heuristic classifier). See `docs/checks.md`.

Three opt-in `VideoRule` checks (each measured only when asked, ADR-016/017/018):

- `check_photosensitivity: true` - ITU-R BT.1702 general flash (20 cd/m2
  changes, darker side < 160 cd/m2, > 25% of the screen, more than
  `max_flashes_per_second` (default 3) in any 1 s) -> `VIDEO_FLASH_RISK`.
  Red flash is not defined by the standard and is reported UNKNOWN.
- `expected_cuts: [seconds]` - compares scdet cut times with your cut
  list (`max_cut_error_frames` default 1, `allow_extra_cuts` default
  true) -> `VIDEO_CUT_MISMATCH`, with matched/missing/extra in evidence.
- `text_regions: [{t, box: [x, y, w, h], role: hook|cta|caption|term}]` -
  WCAG contrast ratio (`min_contrast_ratio` default 4.5) and ink height
  at a 1080 px short side (`min_text_height_px` per role, no default)
  -> `TEXT_ILLEGIBLE`. Measured facts only; no OCR.

Opt-in `SubtitleRule` cue-text and subtitle-vs-speech checks (ADR-023).
None has a default threshold; each runs only when its field is set:

- `max_total_chars` -> `subtitle.total_chars_within_limit`
  (`SUBTITLE_CUE_TOO_MANY_CHARS`, FAIL); `min_cue_chars` ->
  `subtitle.cue_chars_meet_minimum` (`SUBTITLE_CUE_TOO_FEW_CHARS`, WARN
  only). Characters are NFKC code points, line breaks not counted.
- `transcript` (path to a word-timed transcript JSON; `index`/`pos` used
  when present, never required) enables:
  `word_boundary_tolerance_sec` -> `subtitle.cue_boundaries_on_word_boundaries`
  (`SUBTITLE_CUE_BOUNDARY_INSIDE_WORD`), `max_cue_lead_sec` ->
  `subtitle.cue_not_early` (`SUBTITLE_CUE_EARLY`),
  `max_uncovered_speech_sec` (+ `extra_covered_intervals: [{start, end,
  label?}]` for e.g. emphasis graphics) -> `subtitle.speech_covered`
  (`SUBTITLE_SPEECH_UNCOVERED`).
- `forbidden_line_start_pos` (pos labels; needs a transcript with `pos`,
  else UNKNOWN - never guessed) and `forbidden_line_start_phrases`
  (literal, no transcript needed) -> `subtitle.no_forbidden_line_start`
  (`SUBTITLE_FORBIDDEN_LINE_START`), checked on every line.
- `preset: "ja"` fills suggested Japanese values into unset fields only.

Every violation in `evidence.violations` carries `time_sec`, the cue's
`cue_index`/`cue_id`/`text` and a one-line `description`.

One opt-in full-reference comparison (ADR-021), `kind: video` or
`rules.delivery.video`, against the top-level `reference_video`:

- `reference_metrics: ["vmaf", "ssim", "psnr"]` (any subset) ->
  `video.vmaf_meets_minimum` (`min_vmaf` default 93),
  `video.ssim_meets_minimum` (`min_ssim` default 0.95),
  `video.psnr_meets_minimum` (`min_psnr_db` default 31). A smaller input
  is upscaled to the reference's size; a different frame rate or a length
  more than one frame apart is refused (checks UNKNOWN, reason in
  `video.reference_alignment`). Without libvmaf in the host's ffmpeg (see
  doctor `filter:libvmaf`) VMAF is UNKNOWN and SSIM/PSNR still run.

One opt-in stinger check (ADR-024), any `VideoRule`; pass `cut_point`
and `cover` from the stinger's stinger.json:

- `stinger: {cut_point, cover?: {start, end}}` on the stinger file itself
  -> `video.stinger_opaque_at_cut_point` (every pixel's alpha at least
  253/255 at that frame, video-editing-skill's rule;
  `VIDEO_STINGER_NOT_OPAQUE`) and `video.stinger_cut_point_in_cover`
  (`VIDEO_STINGER_CUT_POINT_OUTSIDE_COVER`). No alpha channel, a cut point
  between frames or an unreadable frame is UNKNOWN, never opaque.
- on a delivered video add `file` (the stinger), `cut_at: [seconds]` (the
  join's cut times) and `max_cut_frame_difference` (no default) ->
  `video.stinger_cut_frame_matches` (the delivered frame at each cut vs
  the stinger's covered frame, mean |RGB| 0-255;
  `VIDEO_STINGER_CUT_FRAME_MISMATCH`).

One opt-in loop-seam check for audio (ADR-025), any `AudioRule`:

- `loop_seam: {include_wrap (default true), seams?: [seconds], window_sec?,
  max_click_ratio?, max_level_diff_db?, max_band_diff_db?}` measures the
  wrap (end -> start) and each listed seam: a click ratio (second
  difference at the seam over its 99th percentile around it) ->
  `audio.loop_seam_click_within_limit` (`AUDIO_LOOP_SEAM_CLICK`), the RMS
  step between `window_sec` on each side ->
  `audio.loop_seam_level_within_limit` (`AUDIO_LOOP_SEAM_LEVEL_MISMATCH`),
  and the octave-band shape difference ->
  `audio.loop_seam_spectrum_within_limit`
  (`AUDIO_LOOP_SEAM_SPECTRUM_MISMATCH`). No limit has a default; a seam
  whose windows do not fit, or audio that cannot be decoded, is UNKNOWN.

Opt-in checks for title, stinger and loop material (ADR-026/027/028),
any `VideoRule`; pass the values from the material's title.json /
stinger.json:

- `loop_seam: {window_frames? (default 4), segments?: {in, loop, out},
  max_seam_ratio?}` -> `video.loop_seam_within_limit`
  (`VIDEO_LOOP_SEAM_JUMP`): the loop's last -> first frame step (mean
  |RGB|) over the median step inside it; about 1 when seamless, 2 when a
  frame short. No default limit; a one-frame or undecodable loop is
  UNKNOWN.
- `alpha_edge: {alpha_mode: straight|premultiplied, times?,
  max_fringe_px?}` -> `video.alpha_mode_consistent`
  (`VIDEO_ALPHA_MODE_MISMATCH`: a colour above its alpha in a file
  declared premultiplied) and `video.alpha_edge_clean`
  (`VIDEO_ALPHA_FRINGE`: a dark or light edge wider than the limit when
  composited at the declared mode). No alpha is UNKNOWN.
- `key_fill: {key_file, tolerance? (default 2/255), max_off_fraction?,
  key_range?: limited|full}` on the fill -> `video.key_fill_match`
  (`VIDEO_KEY_FILL_MISMATCH`): size, rate, frame count, start time and
  timecode first, then every frame (key vs the fill's alpha, or the fill
  black outside the key), with the first differing frame.

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
   Before asking for VMAF, check `checks["filter:libvmaf"].status`.
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
