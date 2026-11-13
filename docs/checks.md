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

### Opt-in visual checks: photosensitive flashes, planned cuts, text legibility (ADR-016/017/018)

These three are measured **only when the applicable `VideoRule` asks for
them** (standalone `kind: video`, `delivery.video`, or a
`delivery_package` artifact's `video` rule - no kind-specific plumbing),
so a request that asks for none of them still decodes the file once
(ADR-006). Flash and cut analysis share one extra decode; text regions
decode one frame per region. Pure Python + ffmpeg, no numpy.

**Measurements** (`src/qc_skill/measurements/temporal.py`, `text_legibility.py`):

| id | source | notes |
|---|---|---|
| `video.flash_per_second` | ffmpeg `extractplanes` + area scale to a 32x32 grid | `[{start, end, flashes, changes}]` per 1-second bin, area-qualified (see below); `null` for HDR (PQ/HLG) transfer - no BT.1702 HDR mapping is implemented |
| `video.flash_worst_window` | same | `{start, end, flashes, changes, area_fraction}` - the sliding 1-second window (starting at every frame) with the most area-qualified changes |
| `video.red_flash_count` | not measured | always `null`: BT.1702-3 names "a transition to or from a saturated red" but does not define saturated red (its Annex 3 says more explicit definitions are still required), so qc-skill reports UNKNOWN rather than invent one |
| `video.scene_cuts` | ffmpeg `scdet` | `[{time, frame, score}]` - time/frame index of the first frame of each new shot |
| `video.text_regions` | ffmpeg `crop` of one RGB frame per region | per region: `{index, t, box, role, measured, contrast_ratio, ink_luminance, background_luminance, ink_fraction, text_height_px, line_heights_px, text_height_px_at_1080}` or `{measured: false, reason}` |

Flash measurement - ITU-R BT.1702-3 Guideline 1 (general flash, SDR):
screen luminance `L = 200 * V^2.4` cd/m2 (Annex 2/Table 1: BT.1886,
200 cd/m2 peak white) with `V = (Y - 16) / 219` on 8-bit limited-range
luma (full-range input is converted to limited range first). Per grid
cell, a *change* is an opposing excursion of >= 20 cd/m2 whose darker
side is below 160 cd/m2; a *flash* is a pair of changes
(`flashes = changes / 2`, so 7 changes = 3.5 flashes, "more than three").
A window's area-qualified count is the largest `c` such that cells with
>= `c` changes cover **more than** `flash_area_fraction` (detection
parameter, default 0.25 = BT.1702's 25% of the screen). The grid cell
luminance comes from an area average of code values (gamma domain): exact
for uniform cells, an approximation at edges. These are stated
assumptions about the display, not measurements of one.

Text measurement - one frame at `t`, cropped to `box` `[x, y, w, h]`
(the video's own pixel grid). Otsu's threshold on gamma-encoded luma
splits the box into two classes; the minority class is the ink. Contrast
is WCAG 2.x `(L1 + 0.05) / (L2 + 0.05)` on the classes' median
sRGB-linearised relative luminance. `text_height_px` is the tallest run
of pixel rows containing ink: the cap height for all-caps text without
descenders, an **upper bound** on it otherwise (one box per text line is
the intended use). `text_height_px_at_1080` scales it by
`1080 / min(width, height)`. No OCR, no judgement of the words.

**Policy checks:**

| check_id | rule field(s) | finding |
|---|---|---|
| `video.photosensitive_flash_within_limit` | `check_photosensitivity: true` (opt-in), `max_flashes_per_second` (default 3, BT.1702) | `VIDEO_FLASH_RISK` when the worst window's area-qualified flashes exceed the limit; evidence carries `red_flash: UNKNOWN` |
| `video.cuts_match_expected` | `expected_cuts` (seconds), `max_cut_error_frames` (default 1), `allow_extra_cuts` (default true), `cut_match_window_sec` (default 0.5) | `VIDEO_CUT_MISMATCH` when an expected cut has no detected cut within the window, the max matched error exceeds `max_cut_error_frames`, or (with `allow_extra_cuts: false`) an unplanned cut was detected |
| `video.text_legible` | `text_regions: [{t, box: [x, y, w, h], role: hook\|cta\|caption\|term}]`, `min_contrast_ratio` (default 4.5 = WCAG AA normal text; 7.0 = AAA), `min_text_height_px` (per role, px at a 1080 short side; no default - no opinion) | `TEXT_ILLEGIBLE` when any measured region is below a minimum; `UNKNOWN` when a region could not be measured (box outside the frame, no frame at `t`) and none failed |

Cut matching is deterministic: expected cuts in time order each take the
nearest still-unmatched detected cut within `cut_match_window_sec`; the
check evidence lists `matched` (`{expected, detected, error_frames}`),
`missing`, `extra`, and `max_error_frames`. `scene_cut_threshold`
(detection parameter, default 10 = scdet's own default) sets scdet's
sensitivity.

### Opt-in full-reference metrics: VMAF, SSIM, PSNR (ADR-021)

Measured **only when a `VideoRule` sets `reference_metrics`** (any of
`"vmaf"`, `"ssim"`, `"psnr"`) on `kind: video` or `kind: delivery`
(`rules.delivery.video`); the reference is the request's top-level
`reference_video`, path-checked and fingerprinted like `input` (its
fingerprint joins the report identity and `provenance.reference_video_input`).
Without the rule, `reference_video` stays ignored for these kinds and
nothing changes. One extra ffmpeg run decodes both files and feeds the
pair to `libvmaf`/`ssim`/`psnr` together.

**Measurements** (`src/qc_skill/measurements/reference.py`):

| id | source | notes |
|---|---|---|
| `video.reference_alignment` | ffprobe of both files | `{status: "aligned", compared_size, scaled_distorted_to, pix_fmt, frame_rate, frame_rule, distorted, reference[, psnr_capped]}` or `{status: "refused", code, reason, distorted, reference}`; `null` when no `reference_video` was given |
| `video.vmaf` | ffmpeg `libvmaf` | pooled mean, libvmaf's default model (vmaf_v0.6.1 in libvmaf 2.x); `null` when this ffmpeg has no libvmaf |
| `video.ssim` | ffmpeg `ssim` | `All` mean SSIM (0-1) |
| `video.psnr_db` | ffmpeg `psnr` | `average` PSNR in dB; a bit-identical pair (infinite PSNR) is reported as libvmaf's PSNR ceiling: 60 dB at 8 bit, 72 dB at 10 bit, with `psnr_capped: true` in the alignment |

Alignment rule: both streams are re-timed to start at 0 and compared
frame by frame up to the shorter one. Frame rates must agree within
0.01 fps and frame counts within one frame (durations within one frame
when a count is missing) - otherwise the comparison is **refused**
(`FRAME_RATE_MISMATCH`, `FRAME_COUNT_MISMATCH`, `DURATION_MISMATCH`,
`FRAME_RATE_UNKNOWN`, `LENGTH_UNKNOWN`, `REFERENCE_NO_VIDEO`,
`DISTORTED_NO_VIDEO`), every requested score is `null` and every check is
`UNKNOWN` with that reason. A different **size** is not refused: the
distorted picture is scaled (bicubic) to the reference's size first, as
the VMAF FAQ prescribes. Both sides are converted to the reference's
pixel format when libvmaf accepts it (planar YUV 4:2:0/4:2:2/4:4:4,
8/10 bit), else `yuv420p`.

**Policy checks** (defaults apply only once `reference_metrics` opts in):

| check_id | rule field(s) | finding |
|---|---|---|
| `video.vmaf_meets_minimum` | `reference_metrics` includes `"vmaf"`, `min_vmaf` (default 93 - Rassool, *VMAF Reproducibility*, RealNetworks 2017: about 93 = "indistinguishable from original or with noticeable but not annoying distortion") | `VIDEO_VMAF_BELOW_MINIMUM`; `UNKNOWN` (reason names libvmaf) when this ffmpeg has no libvmaf filter - see `qc doctor` `filter:libvmaf` |
| `video.ssim_meets_minimum` | `"ssim"`, `min_ssim` (default 0.95 - the SSIM-to-MOS mapping of Zinner et al. 2010, as tabulated in Moldovan et al. 2014 Table I: >= 0.95 = MOS 4 "Good") | `VIDEO_SSIM_BELOW_MINIMUM` |
| `video.psnr_meets_minimum` | `"psnr"`, `min_psnr_db` (default 31 - the PSNR-to-MOS mapping of Ke et al. 2008, Moldovan et al. 2014 Table I: >= 31 dB = MOS 4 "Good") | `VIDEO_PSNR_BELOW_MINIMUM` |

A refused alignment, a missing `reference_video` or a missing libvmaf is
`UNKNOWN`, never `PASS` or `FAIL`; a missing libvmaf does not stop SSIM
and PSNR from running.

### Opt-in stinger cut point (ADR-024)

A stinger is a graphic with alpha laid over a join; the clips switch at
its cut point, the frame where the graphic covers the whole picture.
`VideoRule.stinger` (any `VideoRule`: `kind: video`, `delivery.video`, a
`delivery_package` artifact's `video`) checks it. `cut_point` and `cover`
are the stinger maker's statement (stinger.json); the caller reads the
sidecar and passes both - qc-skill opens no sidecar.

| field | meaning |
|---|---|
| `cut_point` | required. Seconds on the stinger's own timeline (from its first frame). It must name a frame: `cut_point * frame_rate` within 1/1000 of a whole frame, else UNKNOWN |
| `cover` | optional `{start, end}`, the window the maker says is fully covered: the frames at `start <= t < end` |
| `file` | optional path to the stinger, resolved and fingerprinted like `input` (its content, not its path, joins the identity; `provenance.stinger_input`). Without it the input **is** the stinger; with it the input is a delivered video and the alpha is read from `file` |
| `cut_at` | optional, needs `file`: seconds on the delivered video's timeline, one per join (video-editing-skill's `stinger.joins[].cut_at`) |
| `max_cut_frame_difference` | needed with `cut_at`, no default: the largest mean absolute difference (8-bit RGB, 0-255) allowed between the delivered frame at a cut and the stinger's frame at its cut point |

**Measurements** (`src/qc_skill/measurements/stinger.py`):

| id | source | notes |
|---|---|---|
| `video.stinger_cut_point_alpha` | ffmpeg `extractplanes=a` of one frame | `{source: input\|stinger_file, cut_point, frame, frame_time, frame_rate, pix_fmt, alpha_format, alpha_bit_depth, width, height, pixels, pixels_below, below_fraction, min_alpha, min_alpha_255, threshold: {min_alpha_255: 253, scale: 255, native}, measured: true}` or `{measured: false, reason}` |
| `video.stinger_cut_frames` | ffmpeg rawvideo RGB of single frames | `{stinger_frame: {frame, frame_time, scaled_to, source_size}, cuts: [{cut_at, frame, frame_time, difference, difference_before, difference_after, measured}]}`; an unmeasurable cut has `{measured: false, reason}` |

The frame at a time `t` is frame `round(t * frame_rate)` of the stream
(exact fractions from ffprobe's `r_frame_rate`). It is decoded by an
input seek to a quarter frame before it, and the decoded frame's own
timestamp must name that frame, else the measurement is refused with the
time it did land on. The alpha plane is read at its native bit depth (8,
10, 12 or 16 bit; ProRes 4444 decodes at 12). **The opaque rule is
video-editing-skill's:** a pixel covers when its alpha is at least 253 of
255 on that scale (`alpha * 255 >= 253 * max`: 253 at 8 bit, 1015 at 10,
4063 at 12, 65021 = 253 * 257 at 16), and the frame covers when no pixel
is below. A stream whose pixel format has no alpha channel (by ffprobe's
own pixel-format flags) is not measured: an alpha that was not read is
never taken as opaque. A VP8/VP9 WebM whose decoder does not expose its
alpha reports `yuv420p` and is UNKNOWN for the same reason.

For a delivered video, each cut frame and its two neighbours are
compared with the stinger's frame at its cut point, scaled (bicubic) to
the delivered size: under full cover the delivered frame is the
stinger's frame, so `difference` is the delivery's encoding loss, while a
stinger placed off the cut shows the clips through it.
`difference_before`/`after` are context only.

**Policy checks:**

| check_id | rule field(s) | finding |
|---|---|---|
| `video.stinger_cut_point_in_cover` | `stinger.cover` | `VIDEO_STINGER_CUT_POINT_OUTSIDE_COVER` when `cut_point` is not in `[start, end)` (the statement contradicts itself; nothing is measured) |
| `video.stinger_opaque_at_cut_point` | `stinger.cut_point` | `VIDEO_STINGER_NOT_OPAQUE` when any pixel of that frame is below 253/255 (evidence: frame, pixels, pixels_below, min_alpha); UNKNOWN when the frame or its alpha could not be read |
| `video.stinger_cut_frame_matches` | `stinger.file`, `cut_at`, `max_cut_frame_difference` | `VIDEO_STINGER_CUT_FRAME_MISMATCH` when a cut frame differs from the stinger's frame by more than the limit (exactly the limit passes); UNKNOWN when a cut frame could not be read and none failed |

Every finding carries the cut time in seconds (`timestamp`) and the
measured values; a violation's `description` reads like `2.000s frame 60
differs from the stinger's frame at its cut point by 40.333 ...`.

Why not `video.cuts_match_expected`: a cut under a stinger is hidden by
design, and scdet reports the graphic's own steps instead. On the test
edit it reports 1.5, 1.933, 2.0, 2.1 and 2.7 s for one join at 2.0 s (a
stinger placed a frame late moves every step by a frame, still within
the default one-frame tolerance); on a smooth wipe it reports nothing.
Neither says where the clips switch.

Suggested `max_cut_frame_difference`: measure your own delivery once. On
the 64x36 test edit (H.264 crf 18) the cut frame measures 0.0 and a
stinger one frame late 40.3 (a quarter of the picture shows through);
for textured graphics in a lossy delivery, start around 2-5 and keep it
well under what a frame off measures.

### Opt-in title, stinger and loop material: loop seam, alpha edges, key / fill (ADR-026/027/028)

Three checks for the material hyperframes-skill (or remotion-ffmpeg-skill,
blender-skill, ffmpeg-skill) renders and video-editing-skill or a
switcher uses. Each is its own `VideoRule` field (any `VideoRule`:
`kind: video`, `delivery.video`, a `delivery_package` artifact's
`video`). The caller reads the material's `title.json` / `stinger.json`
and passes the values on; qc-skill opens no sidecar.

#### Loop seam (`VideoRule.loop_seam`, ADR-026)

| field | meaning |
|---|---|
| `window_frames` | optional, 1-12, default 4: how many frames on each side of the seam the motion inside the loop is read from |
| `segments` | optional, title.json's `{in, loop, out}` in seconds: the loop is the frames `in <= t < in + loop` of the clip; both ends must be on a frame (within 1/1000 of one), else UNKNOWN. Without it the whole file is the loop (a `--segments` loop file, ffmpeg-skill `loop --crossfade`, remotion-ffmpeg-skill `--loop`) |
| `max_seam_ratio` | no default: without it the seam is measured and reported, not judged |

`video.loop_seam` (`src/qc_skill/measurements/video_loop_seam.py`):
`{source: file|segments, frame_rate, window_frames, loop: {first_frame,
last_frame, frames}, seam: {from_frame, to_frame, from_time, to_time,
difference}, inside: [{from_frame, to_frame, difference}], reference,
ratio, still, width, height, measured: true}` or `{measured: false,
reason}`. Differences are the mean absolute difference of two frames in
8-bit RGB (0-255) at the stream's own size; `seam.difference` is the
loop's last frame against its first (what the player shows when it wraps),
`inside` the consecutive pairs in the `window_frames` after the first
frame and before the last, `reference` their median, and `ratio = seam /
reference`. A loop that keeps moving across its seam measures about 1; a
pan cut one frame short shows two frames of motion at once, about 2. On a
still picture (`reference` 0) the ratio is 0 when the seam is still too,
and otherwise `null` with `still: true`, which fails any limit. A file's
frame count is ffprobe's packet count; if the file holds more frames than
that, or the stream ends early, or a frame cannot be decoded, the
measurement is refused with the reason. A loop needs at least 3 frames.

| check_id | rule field | finding |
|---|---|---|
| `video.loop_seam_within_limit` | `loop_seam.max_seam_ratio` | `VIDEO_LOOP_SEAM_JUMP` when the ratio is above the limit (exactly the limit passes) or the seam jumps out of a still picture; evidence: the seam's frames and times, the ratio, the reference and every inside step; `timestamp` is the loop's last frame |

Suggested `max_seam_ratio`: on the 64x36 test loops (H.264 crf 12) a
seamless orbit measures 0.87 and a pan cut on its whole period 1.01;
the same loops one frame short measure 1.60 and 1.99. Start at 1.3.

#### Alpha edges (`VideoRule.alpha_edge`, ADR-027)

| field | meaning |
|---|---|
| `alpha_mode` | required, `straight` or `premultiplied`: the mode the material declares (`title.json` / `stinger.json` `alpha`, or yours) |
| `times` | optional list of 1 to 100 times in seconds (0 or more): the frames to read (each the nearest frame). Default 5 frames spread evenly from the first to the last |
| `max_fringe_px` | optional whole number, no default: without it the fringe is measured, not judged |

`video.alpha_edges` (`src/qc_skill/measurements/alpha_edge.py`):
`{alpha_mode, pix_fmt, frame_rate, frames: [{frame, time, width, height,
pixels, mode: {over_alpha_pixels, worst?: {x, y, alpha, excess}}, fringe:
{partial_pixels, judged_pixels, unjudged_pixels, fringe_pixels,
dark_pixels, light_pixels, width_px, max_deviation: {black, white},
first: {x, y, alpha, deviation}}, measured: true}], measured: true}`;
an unreadable frame is `{frame, measured: false, reason}`. Each frame is
read as 8-bit RGBA.

- **mode**: the pixels whose R, G or B is more than 2/255 above their
  alpha. A premultiplied file has none (its colour is already multiplied
  by alpha, rounding aside); a straight file may have any.
- **fringe**: the partly transparent pixels (0 < alpha < 255) at the
  graphic's edge, composited over black and over white at the declared
  mode (straight: `c * a + bg * (1 - a)`, premultiplied: `c + bg * (1 -
  a)`), against the graphic's own colour composited at the same alpha.
  The graphic's colour is carried in from the opaque pixels: a partly
  transparent pixel touching opaque ones (8 neighbours) takes their mean
  colour, the next ring the mean of the ring before, up to 8 px deep. A
  pixel more than 16/255 off in any channel over either background is a
  fringe pixel, dark when below and light when above. A frame's width is
  the largest, over its fringe pixels, of the shorter of the horizontal
  and vertical runs of fringe pixels through it.

| check_id | rule field(s) | finding |
|---|---|---|
| `video.alpha_mode_consistent` | `alpha_edge.alpha_mode` | `VIDEO_ALPHA_MODE_MISMATCH` when the mode is `premultiplied` and a sampled frame has a colour above its alpha (evidence: every sampled frame's count and worst pixel). `straight` constrains no pixel and passes once the frames are read: a premultiplied file declared straight is told by the fringe check |
| `video.alpha_edge_clean` | `alpha_edge.max_fringe_px` | `VIDEO_ALPHA_FRINGE` when a sampled frame's fringe is wider than the limit (exactly the limit passes), with the width, the pixel count, dark or light and the first fringe pixel; UNKNOWN when a frame has partly transparent pixels but none within 8 px of an opaque one (a soft glow or shadow alone) |

Both are UNKNOWN when the stream's pixel format has no alpha (ffprobe's
own flags), or a sampled frame cannot be read and no frame failed. A
binary alpha (no partly transparent pixel) has no fringe and passes.
Known limit: a soft shadow or outline of another colour drawn right
against opaque fill reads as a fringe of its width; set `max_fringe_px`
above it, or sample frames without it.

On the test material (grey 230, alpha ramping over 5 px) the right mode
measures width 0 (deviation at most 1.1/255); premultiplied read as
straight a 5 px dark fringe (up to 57/255), straight read as
premultiplied a 5 px light one (up to 192/255).

#### Key / fill (`VideoRule.key_fill`, ADR-028)

The input is the fill; the key is `key_fill.key_file`.

| field | meaning |
|---|---|
| `key_file` | required: the key's path, resolved and fingerprinted like `input` (`provenance.key_input`) |
| `tolerance` | optional, of 255, default 2 (ffmpeg-skill's `key_level` tolerance) |
| `max_off_fraction` | optional, default 0: the share of a frame's pixels that may be off before the frame differs |
| `key_range` | optional, `limited` or `full`: how the key's luma maps to 0-255. Default: the key stream's `color_range` (`pc` is full); unknown is read as limited (video black is transparent, video white opaque) |

**Measurements** (`src/qc_skill/measurements/key_fill.py`):

| id | source | notes |
|---|---|---|
| `video.key_fill_pair` | ffprobe | `{fill: {width, height, frame_rate, frames, start_time, timecode, pix_fmt, alpha, color_range}, key: {...}, mismatches: [{field, fill, key}], measured: true}`; frames are packets counted by demuxing, nothing decoded. The timecode is the stream's (else the file's) `timecode` tag; start times half a frame or more apart differ |
| `video.key_fill_frames` | ffmpeg rawvideo, every frame | `{mode: alpha\|black_outside_key, key_range, fill_range?, tolerance, max_off_fraction, frames_compared, pixels_per_frame, differing_frames, first_differing: {frame, time, off_pixels, off_fraction}, listed_differing (first 10), worst}`; `{measured: false, reason}` when the pair does not match or a frame cannot be read |

Frames are decoded in chunks of at most 32 MiB, each frame's timestamp
checked. A YUV or gray key (or fill) is read from its Y plane as stored
(`extractplanes=y`, at its own bit depth) and mapped to 0-255 here: ffmpeg
6.1 and 8.0 scale an untagged plane to different values, so the scaler
is kept out of it. Where the fill carries alpha (`mode: alpha`), a pixel is off when
the key differs from the fill's alpha by more than `tolerance`. Where it
does not (`black_outside_key`, the fill of a key / fill export), a pixel
is off when the key is at most `tolerance` and the fill's luma (read to
0-255 from the fill's range the same way) is above it. Luma, not RGB: a
4:2:2 fill's chroma reaches past every vertical edge of the graphic by
construction, which measured 4% of the pixels "lit" on the moving-box
test pair that a keyer shows clean.

| check_id | rule field | finding |
|---|---|---|
| `video.key_fill_match` | `key_fill.key_file` | `VIDEO_KEY_FILL_MISMATCH` when the size, frame rate, frame count, start time or timecode differ (before any pixel is read; evidence lists each field), or when a frame's share of pixels off is above `max_off_fraction` (the first differing frame, its time and share, how many frames differ); UNKNOWN when the key cannot be read or a frame cannot be decoded |

Measured on ffmpeg-skill's `export --preset key-fill` chain (ProRes 422
HQ fill and key, straight fill): the pair passes with every frame at 0
pixels off; the key a frame late fails at frame 1 (16 pixels, the box's
leading edge); one pixel lit under alpha 0 fails at frame 10 (1 pixel).
A soft alpha through the same chain writes the key's luma at full range
in a stream tagged with no range: read as limited it fails, with
`key_range: "full"` it passes.

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

### Opt-in loop seams (ADR-025)

For audio declared loopable, `AudioRule.loop_seam` (any `AudioRule`:
`kind: audio`, `delivery.audio`, a `delivery_package` artifact's `audio`)
measures the seam where the end runs into the start (the *wrap*) and any
seam times listed inside the file (a bed repeated N times has one every
period). One extra decode to 32-bit float samples at the file's own rate
and channel count; the rest is pure Python.

| field | meaning |
|---|---|
| `include_wrap` | default `true`: measure the wrap (end -> start, joined in memory) |
| `seams` | optional seconds of seams inside the file, e.g. `[8, 16, ..., 72]` for an 8 s loop repeated 10 times |
| `window_sec` | the level/spectrum windows: this long on each side of a seam; no default, needed by the two limits below |
| `max_click_ratio` | limit for `audio.loop_seam_click_within_limit`; no default |
| `max_level_diff_db` | limit for `audio.loop_seam_level_within_limit`; no default |
| `max_band_diff_db` | limit for `audio.loop_seam_spectrum_within_limit`; no default |

**Measurement** (`src/qc_skill/measurements/loop_seam.py`):
`audio.loop_seams` = `{measured, sample_rate, channels, samples,
duration_sec, window_sec, window_samples, seams: [{seam: "wrap" | seconds,
time_sec, click, level, spectrum}]}` (the wrap's `time_sec` is the end of
the file). Each fact is `{measured: true, ...}` or `{measured: false,
reason}` on its own:

- **click** `{ratio, from_silence, peak, reference, step, channel}`. The
  second difference `x[i-1] - 2x[i] + x[i+1]` turns a step or a kink into
  a spike and stays small on smooth material. `ratio` = the largest
  `|second difference|` within +-1 ms of the seam / its 99th percentile
  (nearest rank) within +-50 ms around it, outside that +-1 ms. Continuous
  material measures about 1. When the surroundings are digital silence the
  ratio is 0 for a silent seam and `null` with `from_silence: true` for a
  click out of silence (a violation of any limit). `step` is the sample
  jump across the seam (full scale 1.0). Worst channel.
- **level** `{before_dbfs, after_dbfs, diff_db, channel}`: RMS of the last
  `window_sec` before the seam and the first `window_sec` after it, in
  dBFS (`20 log10 rms`, floored at -120). Worst channel.
- **spectrum** `{max_diff_db, bands: [{centre_hz, before_db, after_db,
  diff_db, compared}], bands_compared, frame_samples, before_total_db,
  after_total_db, channel}`: octave bands centred on 62.5 Hz * 2^k (each a
  factor of two wide; the top one may be cut off at the Nyquist
  frequency, so every frequency up to it is in a band) from Hann-windowed,
  non-overlapping frames of a power of two near a tenth of a second, the
  whole frames next to the seam. Each band is taken relative to its side's
  total, so the spectrum compares **shape**: a level change alone is the
  level fact's. Bands more than 30 dB below the loudest band (over both
  sides) are not compared. A side that is digital silence has no shape:
  `max_diff_db` is 0 when both sides are, `null` with `silent_side` when
  one is (a violation). Worst channel.

A seam whose windows do not fit in the file (the wrap of a file shorter
than two windows, a seam closer than a window to an end, a seam past the
end, less than 50 ms of audio around it for the click, a `window_sec`
shorter than two samples, samples there that are not finite numbers) is
not measured for that fact, with the reason. Nothing is padded.

**Policy checks** (each only when its limit is set; exactly the limit
passes; values are compared as reported):

| check_id | rule field | finding |
|---|---|---|
| `audio.loop_seam_click_within_limit` | `max_click_ratio` | `AUDIO_LOOP_SEAM_CLICK` |
| `audio.loop_seam_level_within_limit` | `max_level_diff_db` (+ `window_sec`) | `AUDIO_LOOP_SEAM_LEVEL_MISMATCH` |
| `audio.loop_seam_spectrum_within_limit` | `max_band_diff_db` (+ `window_sec`) | `AUDIO_LOOP_SEAM_SPECTRUM_MISMATCH` |

Each check covers every seam: FAIL when any seam is over the limit,
UNKNOWN when none is but a seam could not be measured (or nothing could
be: no audio stream, no samples, a decode failure, more than 128 MiB of
float samples - ffmpeg is told to stop writing there), PASS otherwise. `evidence.violations` is sorted by time; each
has `seam`, `time_sec`, the measured values and a `description` such as
`2.003s wrap (end -> start): click ratio 12.2217 on channel 0 ...`; the
finding's `timestamp` is the first violation's time.

**No default limits.** Suggested starting points, from the test
fixtures (16 kHz tones): a continuous wrap measures a click ratio of about
0.94, a loop cut 3 ms off a whole cycle 12.2, a phase flip on one channel
20.4 - start `max_click_ratio` around 3. With `window_sec` 1 (long enough
that a beat right after the seam averages out; much shorter windows judge
the music's own movement), start `max_level_diff_db` around 1.5 and
`max_band_diff_db` around 6.

## Subtitle (`kind: "subtitle"`, and `kind: "delivery"` via the `subtitle` field)

Formats: SRT, WebVTT, ASS/SSA (cue timing and text only - styling/tags are
stripped, never evaluated). Content/wording is never judged by an AI or
otherwise; the opt-in ADR-023 checks below only compare cue text
character by character with words and phrases the caller supplies.

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

### Cue text limits and subtitle-vs-speech checks (ADR-023)

Six opt-in `SubtitleRule` checks. Each runs only when its own field is
set; with none set, nothing below is measured or reported. Like
`timeline_integrity`, they work wherever a `SubtitleRule` nests
(`kind: "subtitle"`, `delivery.subtitle`, a `delivery_package`
artifact's `subtitle`).

The three speech checks need `transcript`: a path to a word-timed
transcript JSON (transcription-skill's format). It is resolved through
the same path policy as every other input, and its content fingerprint
(not its path) joins the request identity. qc-skill reads it and never
produces one. Without it, or when it cannot be read, those checks are
`UNKNOWN` with a reason.

**Transcript reading** (`src/qc_skill/measurements/transcript.py`) is
tolerant of older and newer files:

- words come from `segments[].words[]`; a top-level `words` list is used
  when no segment carries words; a top-level JSON list is read as
  segments (or as words);
- a word's text is `text`, or `word` when `text` is absent;
- `index` (0-based, whole transcript) is kept when it is an integer;
  every word also gets `position`, its 0-based reading order counted over
  all word entries, so it equals `index` when the file has one;
- `pos` is read when present (lower-cased) and never inferred; the
  labels qc-skill knows are `noun, pronoun, verb, adjective, adverb,
  particle, auxiliary, conjunction, interjection, prefix, suffix, symbol,
  other`;
- `timing: "interpolated"` (transcription-skill 0.9.0+, a
  character-share estimate on dictionary-segmented words) marks an
  estimated timing; `"measured"` or no `timing` key is a measured /
  engine timing; a boolean `interpolated`, `timing_interpolated` or
  `estimated` set to `true` counts as interpolated too;
- a segment with `words: null` contributes no words;
- a transcript with no usable word at all (e.g. one made without word
  timestamps) counts as unreadable, so the speech checks are `UNKNOWN`
  instead of passing on zero words;
- a word without text, or without numeric finite `start`/`end` with
  `end >= start`, is skipped and counted.

**Measurements** (opt-in): `subtitle.cue_texts` (per cue, in file
order: `{index, cue_id, start, end, lines, chars}`; `lines` are the
visible text with SRT/WebVTT tags and `{...}` override blocks removed,
WebVTT character references decoded, each line stripped),
`subtitle.transcript_words` (`[{position, index, text, start, end, pos,
interpolated}]`, or `null` with the read error in `notes`),
`subtitle.transcript_word_count`, `subtitle.transcript_skipped_word_count`,
`subtitle.transcript_pos_word_count`,
`subtitle.transcript_interpolated_word_count`.

**How cues and words are matched.**

- *Time:* a word belongs to the cue on screen at the word's midpoint
  (`start <= midpoint < end`; with overlapping cues, the latest-starting
  one). A word between cues belongs to none.
- *Text:* cue lines and words are compared after NFKC normalisation and
  case folding, with every punctuation, symbol, space and control
  character removed. A cue is *aligned* when its words, in transcript
  order, spell its text exactly in that form; then each line's start
  maps to the word that starts there. A cue whose words differ in any
  way (edited wording, numerals written differently, a word cut by the
  cue boundary) is not aligned, and nothing is guessed for it.

**Character counting** (`max_total_chars`, `min_cue_chars`): Unicode code
points of each visible line after NFKC normalisation, line breaks not
counted, spaces inside a line counted. Every code point counts as one:
a full-width kanji and a Latin letter both count 1, and half-width
katakana count as their full-width forms (`ｶﾞ` is one character). It is
not a display-width measure. The existing `max_line_length` keeps its own
counting (raw code points per line, markup included).

**Policy checks:**

| check_id | rule field(s) | status / finding |
|---|---|---|
| `subtitle.cue_boundaries_on_word_boundaries` | `transcript`, `word_boundary_tolerance_sec` | FAIL `SUBTITLE_CUE_BOUNDARY_INSIDE_WORD` when a cue start or end lies inside a word by more than the tolerance at both edges (`word.start + tol < t < word.end - tol`) |
| `subtitle.cue_not_early` | `transcript`, `max_cue_lead_sec` | FAIL `SUBTITLE_CUE_EARLY` when the first word assigned to a cue starts more than `max_cue_lead_sec` after the cue appears (exactly the limit passes); cues with no words are counted in `cues_without_speech` |
| `subtitle.speech_covered` | `transcript`, `max_uncovered_speech_sec`, `extra_covered_intervals: [{start, end, label?}]` | FAIL `SUBTITLE_SPEECH_UNCOVERED` when, inside a stretch with no cue and no extra covered interval, speech runs longer than `max_uncovered_speech_sec` (first word start to last word end, clipped to the stretch) |
| `subtitle.no_forbidden_line_start` | `forbidden_line_start_pos` (any of the labels above), `forbidden_line_start_phrases` | FAIL `SUBTITLE_FORBIDDEN_LINE_START` when a line starts with a word whose `pos` is listed, or with a listed phrase |
| `subtitle.total_chars_within_limit` | `max_total_chars` | FAIL `SUBTITLE_CUE_TOO_MANY_CHARS` when a cue has more characters |
| `subtitle.cue_chars_meet_minimum` | `min_cue_chars` | **WARN** `SUBTITLE_CUE_TOO_FEW_CHARS` when a non-empty cue has fewer characters (short replies are legitimate; empty cues are `subtitle.no_empty_cues`' job) |

`subtitle.no_forbidden_line_start` looks at **each line** of each cue,
not only the first. The phrase part needs no transcript: a phrase
matches when the line, in the comparison form above, starts with it; when
the cue is aligned with transcript words, the phrase must also end on a
word boundary (so `は` does not match the single word `はい`), and
evidence says which (`phrase_matched_on: word_boundary | text_prefix`).
The pos part needs a transcript with `pos`; it is never guessed from
characters (a leading `が` can begin a noun). When it cannot be decided -
no transcript, no `pos` labels in it, a cue that is not aligned, a line
that starts inside a word, a word without `pos` - the check is `UNKNOWN`
(unless a confirmed violation makes it FAIL), and `evidence.pos_part`
plus `evidence.unverified` say why, line by line.

A violation that rests on an interpolated word timing is not confirmed:
it is listed under `evidence.unconfirmed` and makes the check `UNKNOWN`
instead of FAIL, unless confirmed violations exist too.

**Evidence.** Each check's `evidence.violations` (also in the finding's
evidence) is sorted by time; every entry carries `time_sec` (where to
jump), the cue's `cue_index` (1-based file order), `cue_id`, `start`,
`end` and `text`, the involved words (`position`, `index`, `text`,
`start`, `end`, `pos`), and a `description` line such as
`12.340s cue #5 (id 5) "Hello world" starts 0.120s inside the word "Hello"
(12.220-12.600s)`. The finding's `timestamp` is the first violation's
`time_sec`.

**No default thresholds.** Every threshold is a style choice, so none
has a default. Starting points to consider: `word_boundary_tolerance_sec`
0.04-0.1 (about one to two frames, within typical word-timing jitter),
`max_cue_lead_sec` 0.3-0.5, `max_uncovered_speech_sec` 1.0-2.0.

**`preset: "ja"`** (opt-in; the default assumes no language) fills only
the fields a rule leaves unset: `forbidden_line_start_pos: [particle,
auxiliary]`, `forbidden_line_start_phrases: [を, に, が, って, けど, ので,
です, ます, でした, ました, と思います]`, `max_total_chars: 32` (two
lines of 16), `min_cue_chars: 4`. It sets no timing threshold. The
contract lists the presets and pos labels under `subtitle_speech`.

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
