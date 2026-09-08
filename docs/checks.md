# Measurements, checks, findings

This is the human-readable version of what `qc contract --json` reports.
The contract is authoritative and generated from the same source
(`src/qc_skill/contract.py`); nothing listed here is aspirational - if it
isn't in the contract, it isn't implemented yet. `tests/test_contract_completeness.py`
diffs the contract against the actual source of `rules.py` and
`measurements/*.py` in both directions, so this document, the contract,
and the implementation cannot silently drift apart.

The contract also exposes two machine-readable sections not repeated in
full here: `contract["rules"]` - the accepted field name/type/default for
`VideoRule`/`AudioRule`/`SubtitleRule`/`DeliveryRule`, generated directly
from the dataclasses in `rules.py` (so it is never a stale second copy)
- and `contract["findings"]` - every finding code this skill can emit,
with its category and default severity.

## Video (`kind: "video"`, and `kind: "delivery"` when a video stream is present)

**Measurements** (`src/qc_skill/measurements/video.py`):

| id | source | notes |
|---|---|---|
| `container.format_name`, `container.duration_sec`, `container.size_bytes`, `container.bit_rate` | ffprobe | |
| `video.stream_present`, `video.stream_count` | ffprobe | |
| `video.codec`, `video.width`, `video.height`, `video.pixel_format` | ffprobe | |
| `video.aspect_ratio` | ffprobe (or derived from width/height, `estimated: true`) | |
| `video.frame_rate` | ffprobe | |
| `video.variable_frame_rate_suspected` | ffprobe:derived | `true` when `\|r_frame_rate - avg_frame_rate\| > 0.01` fps (mirrors ffmpeg-skill's own `variable_frame_rate_suspected` heuristic); `null` when either rate is unavailable - never guessed |
| `video.frame_count` | ffprobe `nb_frames`, or derived from `duration * frame_rate` (`estimated: true`) when absent | |
| `video.color_range`, `video.color_space`, `video.color_transfer`, `video.color_primaries`, `video.field_order` | ffprobe | `null` when the container doesn't carry it - never guessed |
| `video.black_segments` | ffmpeg `blackdetect` | `[{start, end, duration}]` |
| `video.freeze_segments` | ffmpeg `freezedetect` | a segment still frozen at end-of-stream has `end: null, duration: null` (not fabricated) |
| `video.luminance_excursions` | ffmpeg `signalstats` | `[{start, end, duration, min_y, max_y}]` - contiguous runs where Y fell outside `[luminance_legal_min, luminance_legal_max]` (default 16/235); a literal per-frame pixel readout, not a heuristic classifier (ADR-013) |
| `video.decoded_frame_count`, `video.decode_error_count`, `video.decode_errors`, `video.frame_count_delta` | ffmpeg full decode | one decode pass produces black/freeze/luminance/integrity together |

**Baseline checks** (always run for `check`/`validate`, no rule needed):

| check_id | fails when | finding |
|---|---|---|
| `video.stream_present` | no video stream (standalone `kind: video`; overridable via `delivery.require_video` for delivery) | `VIDEO_STREAM_MISSING` |
| `video.decodes_without_errors` | decode error count exceeds `VideoRule.max_decode_errors` (default 0) | `VIDEO_DECODE_ERROR` |

**Policy checks** (only when the matching `VideoRule` field is set):

| check_id | rule field(s) | finding |
|---|---|---|
| `video.duration_within_limit` | `max_duration_sec` | `VIDEO_DURATION_EXCEEDED` |
| `video.resolution_matches_expected` | `expected_width`, `expected_height` | `VIDEO_RESOLUTION_MISMATCH` |
| `video.resolution_meets_minimum` | `min_width`, `min_height` (an "at least" form alongside the exact-equality check above, e.g. "short side >= 1080, any exact size") | `VIDEO_RESOLUTION_BELOW_MINIMUM` |
| `video.frame_rate_matches_expected` | `expected_frame_rate`, `frame_rate_tolerance` | `VIDEO_FPS_MISMATCH` |
| `video.frame_rate_is_constant` | `disallow_vfr` | `VIDEO_VARIABLE_FRAME_RATE` |
| `video.codec_matches_expected` | `expected_codec` | `VIDEO_CODEC_MISMATCH` |
| `video.pixel_format_matches_expected` | `expected_pixel_format` | `VIDEO_PIXEL_FORMAT_MISMATCH` |
| `video.aspect_ratio_matches_expected` | `expected_aspect_ratio` | `VIDEO_ASPECT_MISMATCH` |
| `video.color_range_matches_expected` | `expected_color_range` | `VIDEO_COLOR_RANGE_MISMATCH` |
| `video.color_space_matches_expected` | `expected_color_space` | `VIDEO_COLOR_SPACE_MISMATCH` |
| `video.color_transfer_matches_expected` | `expected_color_transfer` | `VIDEO_COLOR_TRANSFER_MISMATCH` |
| `video.color_primaries_matches_expected` | `expected_color_primaries` | `VIDEO_COLOR_PRIMARIES_MISMATCH` |
| `video.black_frames_within_tolerance` | `max_single_black_sec`, `max_total_black_sec` | `VIDEO_BLACK_FRAMES_EXCEEDED` |
| `video.freeze_frames_within_tolerance` | `max_single_freeze_sec`, `max_total_freeze_sec` | `VIDEO_FREEZE_EXCEEDED` (or `UNKNOWN` if an unresolved freeze-to-EOF segment exists and no violation was found from the resolvable segments) |
| `video.luminance_within_legal_range` | `max_single_luminance_excursion_sec`, `max_total_luminance_excursion_sec` | `VIDEO_LUMINANCE_OUT_OF_RANGE` |

The color/HDR checks are plain exact-equality comparisons against
`video.color_range`/`color_space`/`color_transfer`/`color_primaries` -
the same mechanism as `expected_codec`/`expected_pixel_format`. A common
use is flagging HDR delivered to an SDR-only platform: setting
`expected_color_transfer: "bt709"` FAILs against an HDR source (whose
transfer is `smpte2084`/PQ or `arib-std-b67`/HLG).

`video.frame_rate_is_constant` mirrors ffmpeg-skill's own
`variable_frame_rate_suspected` heuristic (`video.variable_frame_rate_suspected`
above); it only runs when `disallow_vfr` is set, and is `UNKNOWN` (never
a guessed PASS/FAIL) when the underlying rates could not both be
measured.

`luminance_legal_min`/`luminance_legal_max` (default 16/235, the
near-universal legal range for 8-bit limited-range video) are *detection*
parameters (`parameters`, like `black_pixel_threshold`) - they decide
what counts as an excursion worth measuring, not whether one is
acceptable. Whether any excursion is acceptable is always the caller's
call via `max_single_luminance_excursion_sec`/
`max_total_luminance_excursion_sec` - qc-skill never asserts that 16-235
is required (a full-range web/streaming delivery may legitimately use
0-255).

## Audio (`kind: "audio"`, and `kind: "delivery"` when an audio stream is present)

**Measurements** (`src/qc_skill/measurements/audio.py`):

| id | source |
|---|---|
| `audio.stream_present`, `audio.stream_count`, `audio.codec`, `audio.sample_rate`, `audio.channels`, `audio.channel_layout`, `audio.duration_sec` | ffprobe |
| `audio.peak_level_dbfs`, `audio.rms_level_dbfs`, `audio.clipping_detected`, `audio.per_channel_levels` | ffmpeg `astats` |
| `audio.integrated_loudness_lufs`, `audio.loudness_range_lu`, `audio.true_peak_dbfs` | ffmpeg `ebur128` |
| `audio.silence_segments`, `audio.leading_silence_sec`, `audio.trailing_silence_sec`, `audio.internal_silence_segments` | ffmpeg `silencedetect`, classified into leading/trailing/internal |
| `audio.decode_error_count`, `audio.decode_errors` | ffmpeg decode | one ffmpeg pass produces astats+ebur128+silencedetect+decode-errors together |

Clipping is a heuristic: `peak_level_dbfs >= clipping_threshold_dbfs`
(default `-0.1`, a detection-sensitivity parameter, not a policy) - this
version of ffmpeg's `astats` does not expose a literal per-sample clipped
count.

**Baseline checks:**

| check_id | fails when | finding |
|---|---|---|
| `audio.decodes_without_errors` | decode error count exceeds `AudioRule.max_decode_errors` (default 0) | `AUDIO_DECODE_ERROR` |
| `audio.no_clipping` | `audio.clipping_detected` is true | `AUDIO_CLIPPING_DETECTED` |

**Policy checks:**

| check_id | rule field(s) | finding |
|---|---|---|
| `audio.stream_present_matches_expected` | `require_audio_stream` | `AUDIO_STREAM_MISSING` / `AUDIO_STREAM_UNEXPECTED` |
| `audio.sample_rate_matches_expected` | `expected_sample_rate` | `AUDIO_SAMPLE_RATE_MISMATCH` |
| `audio.channels_match_expected` | `expected_channels` | `AUDIO_CHANNELS_MISMATCH` |
| `audio.channel_layout_matches_expected` | `expected_channel_layout` | `AUDIO_CHANNEL_LAYOUT_MISMATCH` |
| `audio.leading_silence_within_tolerance` | `max_leading_silence_sec` | `AUDIO_LEADING_SILENCE_EXCEEDED` |
| `audio.trailing_silence_within_tolerance` | `max_trailing_silence_sec` | `AUDIO_TRAILING_SILENCE_EXCEEDED` |
| `audio.internal_silence_within_tolerance` | `max_internal_silence_sec` | `AUDIO_INTERNAL_SILENCE_EXCEEDED` |
| `audio.integrated_loudness_within_tolerance` | `integrated_loudness_target_lufs`, `integrated_loudness_tolerance_lu` | `AUDIO_LOUDNESS_OUT_OF_RANGE` |
| `audio.true_peak_within_tolerance` | `max_true_peak_dbfs` | `AUDIO_TRUE_PEAK_EXCEEDED` |
| `audio.loudness_range_within_tolerance` | `max_loudness_range_lu` | `AUDIO_LOUDNESS_RANGE_EXCEEDED` |
| `audio.channel_balance_within_tolerance` | `max_channel_level_diff_db` | `AUDIO_CHANNEL_IMBALANCE` (or `AUDIO_CHANNEL_MISSING` when the quieter channel is at/below -90 dBFS RMS) |

`qc-skill` never hard-codes a loudness target (e.g. "-14 LUFS for
YouTube") - `integrated_loudness_target_lufs` and every other target must
come from the request.

## Subtitle (`kind: "subtitle"`, and `kind: "delivery"` via the `subtitle` field)

Formats: SRT, WebVTT, ASS/SSA (cue timing and text only - styling/tags are
stripped, never evaluated). Content/wording is never evaluated by an AI or
otherwise.

**Measurements** (`src/qc_skill/measurements/subtitle.py`): `subtitle.exists`,
`subtitle.format`, `subtitle.cue_count`, `subtitle.cues` (raw parsed
per-cue `{index, start, end}`, in file order), `subtitle.invalid_timestamps`,
`subtitle.overlapping_cues`, `subtitle.empty_cues`, `subtitle.duplicate_ids`,
`subtitle.invalid_control_characters`, `subtitle.duration_sec`,
`subtitle.coverage_ratio` (requires a reference video duration),
`subtitle.duration_delta_sec`, `subtitle.cue_density_per_min`,
`subtitle.excessive_line_length`, `subtitle.excessive_cue_duration`,
`subtitle.gaps`.

**Baseline checks** (structurally malformed cues are always a defect,
regardless of policy):

| check_id | fails when | finding |
|---|---|---|
| `subtitle.timestamps_valid` | any cue has an invalid/unparsable timestamp or start > end | `SUBTITLE_INVALID_TIMESTAMP` |
| `subtitle.no_empty_cues` | any cue has no visible text (WARN) | `SUBTITLE_EMPTY_CUE` |
| `subtitle.no_duplicate_ids` | duplicate explicit cue identifiers (unless `allow_duplicate_ids`) | `SUBTITLE_DUPLICATE_ID` |
| `subtitle.no_control_characters` | a cue contains control characters | `SUBTITLE_CONTROL_CHARACTER` |
| `subtitle.no_overlapping_cues` | two cues overlap in time (unless `allow_overlapping_cues`) | `SUBTITLE_OVERLAPPING_CUES` |

**Policy checks:**

| check_id | rule field(s) | finding |
|---|---|---|
| `subtitle.presence_matches_expected` | `require_subtitle` | `SUBTITLE_MISSING` |
| `subtitle.line_length_within_limit` | `max_line_length` | `SUBTITLE_LINE_TOO_LONG` |
| `subtitle.cue_duration_within_limit` | `max_cue_duration_sec` | `SUBTITLE_CUE_TOO_LONG` |
| `subtitle.gaps_within_limit` | `max_gap_sec` | `SUBTITLE_GAP_EXCEEDED` |
| `subtitle.duration_matches_video` | `max_duration_delta_sec` (needs `reference_video`) | `SUBTITLE_DURATION_MISMATCH` |
| `subtitle.coverage_within_limit` | `min_coverage_ratio` (needs `reference_video`) | `SUBTITLE_COVERAGE_LOW` |

### Timeline integrity (`timeline_integrity`, Phase 3, ADR-012)

`SubtitleRule.timeline_integrity` (`TimelineIntegrityRule`: `timeline` -
a caller-supplied list of `TimelineSegment {source_start, source_end,
delivery_start, speed}`; `source_cues` - the cue timing as authored
against the *source* timeline before the edit; `tolerance_sec`) checks
whether this subtitle's actual delivery-timeline cue timing
(`subtitle.cues`) still matches what the supplied timeline mapping says
it should be, after a trim/concat/speed edit. qc-skill never constructs
or infers the timeline itself - it only maps a given source timestamp
through segments it was handed.

| check_id | fails when | finding |
|---|---|---|
| `subtitle.timeline_mapping_matches_source` | actual cue count differs from `source_cues` count, or any mapped cue's expected delivery position differs from its actual position by more than `tolerance_sec` | `SUBTITLE_TIMELINE_CUE_COUNT_MISMATCH` / `SUBTITLE_TIMELINE_MAPPING_MISMATCH` |

`UNKNOWN` (never a guessed `PASS`/`FAIL`) when a source cue falls
entirely inside a cut region - no supplied segment covers it, so its
expected delivery position cannot be computed. Because this is a
`SubtitleRule` field, it is available anywhere a `SubtitleRule` nests:
standalone `kind: "subtitle"`, `kind: "delivery"`'s `subtitle` sub-rule,
and `kind: "delivery_package"`'s per-artifact `subtitle` sub-rule - no
kind-specific plumbing was added for it.

## Delivery (`kind: "delivery"`)

Combines the video/audio/subtitle checks above (each only if the caller
supplies `require_video`/`require_audio`/`require_subtitle` and/or a
nested `video`/`audio`/`subtitle` rule) with delivery-specific checks:

| check_id | rule field(s) | finding |
|---|---|---|
| `delivery.file_size_within_limit` | `min_size_bytes`, `max_size_bytes` | `DELIVERY_FILE_TOO_SMALL` / `DELIVERY_FILE_TOO_LARGE` |
| `delivery.extension_matches_expected` | `expected_extension` | `DELIVERY_EXTENSION_MISMATCH` |
| `delivery.container_matches_expected` | `expected_container` | `DELIVERY_CONTAINER_MISMATCH` |

A delivery-level `require_video` / `require_audio` / `require_subtitle`
folds into the corresponding sub-rule's own requirement field unless the
sub-rule already specifies one explicitly.

## Delivery package (`kind: "delivery_package"`)

Validates N named artifacts (`request.artifacts`: `{artifact_id,
artifact_type, path}`) as one delivery, paired against
`rules.delivery_package.artifacts` (`DeliveryArtifactRule`) by
`artifact_id`. See `docs/architecture.md` and ADR-010
(`docs/decisions.md`) for why this is a separate kind from `delivery`
rather than an extension of it. `artifact_type` is one of `video`,
`audio`, `subtitle`, `thumbnail`, `metadata`, `other`; only the first
three get type-specific measurement/checks (they reuse the same
video/audio/subtitle machinery as the other kinds) - `thumbnail`,
`metadata`, and `other` only get the generic checks below.

**Measurements** (`src/qc_skill/engine.py::_run_delivery_package`, per
artifact): `delivery_package.artifact_present`,
`delivery_package.artifact_size_bytes`, `delivery_package.artifact_extension`,
`delivery_package.artifact_fingerprint` (sha256, always computed by
qc-skill itself - never accepted from the caller), plus the full
`video.*`/`audio.*`/`subtitle.*` measurement set for artifacts of those
types. Every measurement carries the producing `artifact_id` so that,
e.g., two subtitle artifacts in the same package don't collide.

**Checks** (per artifact, only for artifact ids named in
`rules.delivery_package.artifacts`):

| check_id | rule field(s) | finding |
|---|---|---|
| `delivery_package.artifact_present` | `required` (default `true`) | `DELIVERY_PACKAGE_ARTIFACT_MISSING` |
| `delivery_package.artifact_size_within_limit` | `min_size_bytes` | `DELIVERY_PACKAGE_ARTIFACT_TOO_SMALL` |
| `delivery_package.artifact_extension_matches_expected` | `expected_extension` | `DELIVERY_PACKAGE_ARTIFACT_EXTENSION_MISMATCH` |

A nested `video` / `audio` / `subtitle` rule on a `DeliveryArtifactRule`
runs the same checks as the standalone `kind`s above, against that one
artifact's own measurements only - never compared against any other
artifact in the package.

### Cross-artifact validation (`rules.delivery_package.cross_artifact`)

Typed relationship rules comparing *different* artifacts against each
other - never a string comparison or LLM judgment (ADR-011,
`docs/decisions.md`). Both operate purely on measurements each artifact's
own gathering already produced.

| check_id | rule field(s) | finding |
|---|---|---|
| `delivery_package.duration_consistent` | `cross_artifact.duration_consistency[].{artifact_ids, max_delta_sec}` | `DELIVERY_PACKAGE_DURATION_MISMATCH` |
| `delivery_package.dependency_satisfied` | `cross_artifact.dependencies[].{artifact_id, requires_artifact_id}` | `DELIVERY_PACKAGE_DEPENDENCY_MISSING` |

`duration_consistency` compares `container.duration_sec`
(video/audio artifacts) or `subtitle.duration_sec` (subtitle artifacts)
across the named `artifact_ids`; `UNKNOWN` (not a guessed `PASS`/`FAIL`)
when any named artifact's duration could not be measured.
`dependencies` only fires when `artifact_id` is actually present - an
absent dependent artifact has nothing to enforce, since
`delivery_package.artifact_present` (above) already covers that case on
its own.

Semantic comparisons (does the subtitle's wording match the video, does
the thumbnail "look like" the video) are explicitly out of scope
everywhere in qc-skill, not just here - see `not_provided` in
`qc contract --json`.
