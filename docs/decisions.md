# Decisions

Short rationale for choices that would otherwise look arbitrary.

## ADR-001: qc-skill never produces a Decision

`qc-skill` returns `QCReport` (checks/measurements/findings +
`overall_status`), never a `video-production-agent`-style `Decision`,
`Inference`, or `ProductionPlan`. A `FAIL` is data, not an instruction to
block, re-render, or delete anything. This keeps the responsibility
boundary from STEP 13 of the task spec unambiguous: measurement and
judgment against an explicit rule stay in `qc-skill`; what to *do* about a
finding is always the agent's call.

## ADR-002: Measurement / Rule / Finding are separate types

Collapsing "what we measured" and "whether it's acceptable" into one
value (e.g. a single boolean `loudness_ok`) throws away the evidence a
caller needs to explain a result, and makes it impossible to re-evaluate
the same measurement against a different target without re-running
ffmpeg. `QCMeasurement` is never rewritten by rule evaluation; `Rule` is
always caller-supplied typed data (`src/qc_skill/rules.py`), never a
hard-coded constant; `QCFinding` is the explicit output of evaluating one
against the other.

## ADR-003: `skill_id = "qc"`, package/repo `qc-skill`

Following the sibling skills' convention of a short, hyphen-free
`skill_id`/CLI-program name distinct from the (longer) pip package/repo
name (`media-analysis-skill` -> `media-analysis`; `audio-production-skill`
-> `audio-production`). Tool/check ids are `qc/<name>`; Observation
`source` strings (when wrapped by an agent) follow `qc/<kind>@<version>`.

## ADR-004: worst-wins status aggregation with UNKNOWN above WARN

`QCStatus.aggregate` orders severity `FAIL > UNKNOWN > WARN > PASS`. An
`UNKNOWN` check (couldn't be verified) is treated as more significant than
a confirmed `WARN`, because an unverified check is strictly less
informative than one that ran and only found a minor issue - it must never
be silently outranked by, or hidden behind, a `WARN`. `UNKNOWN` is still
below `FAIL`: a confirmed violation is always the worst outcome.

## ADR-005: detection sensitivity vs. policy are different kinds of parameters

`black_min_duration_sec`, `silence_threshold_db`, `clipping_threshold_dbfs`,
etc. are *detection* parameters - "how sensitive is the measurement",
with a sensible, documented default. `expected_width`,
`integrated_loudness_target_lufs`, `max_single_black_sec`, etc. are
*policy* - "is this measurement acceptable", and have **no** default: a
`Rule` field left `None` means "no opinion," and the corresponding check
simply isn't produced. Mixing the two would either force a policy default
qc-skill has no business choosing (STEP 5 of the task spec explicitly
forbids hard-coding "YouTube is -14 LUFS"), or make detection unusable
without specifying an opinion about acceptability first.

## ADR-006: one full decode pass per video/audio stream, not per detector

`analyze_video_defects` runs `blackdetect` + `freezedetect` + decode-error
scanning + frame counting in a single ffmpeg invocation;
`analyze_audio` runs `astats` + `ebur128` + `silencedetect` + decode-error
scanning in a single invocation. All of these filters are pass-through
(they don't alter the stream), so chaining them costs one decode instead
of three or four. This matters for both wall-clock time and for keeping
identity/caching simple (one operation's parameters determine the cache
key, not four).

## ADR-007: baseline checks are still checks, not implicit contract obligations

Decode-integrity and clipping are evaluated even with no `Rule` at all,
because "does this file decode cleanly" and "did anything clip" are
objective invariants, not policy choices - unlike resolution, fps, or
loudness targets, which vary by delivery context. This mirrors
`ffmpeg-skill/check.py`'s `kind: "format"` vs. `kind: "judgement"` split
and `video-production-agent`'s `QAItem.kind` vocabulary, without adopting
either verbatim.

## ADR-008: reuse is validated on every read, not trusted on presence

A cached report is only ever returned when its own recomputed content hash
matches what was stored (`QCReportCache.get`). This mirrors
`media-analysis-skill`'s `ObservationCache` and exists specifically so a
hand-edited or corrupted cache file cannot be reported back as a
successful QC verdict - STEP 21 of the task spec is explicit that reuse
must re-verify report integrity, not just re-verify that a file exists.

## ADR-009: qc-skill evolves toward multi-skill pipeline QC, not toward being a better single-file checker

Full evidence for this decision is in `docs/qc-evolution-gap-analysis.md`
(capability matrix against QCTools, MediaConch, VMAF, rendercheck,
uploadcheck-mcp, Kinocut, plus an ecosystem read of all 8 neighboring
repos). Summary of the decision:

- "Agent-native QC" is **not** a differentiator - `uploadcheck-mcp` and
  Kinocut already ship that, and copying either would not close a real
  gap. The genuinely open gaps, confirmed by direct comparison, are:
  cross-artifact validation across a *multi-skill* pipeline's
  heterogeneous outputs; genuine timeline-awareness of edit history
  (source-timeline -> delivery-timeline, not just source-file checks);
  deterministic evidence chained across *multiple* tools' outputs, not
  one tool's own steps; and an orchestrator-facing evidence graph tying a
  QC verdict into the agent's Decision loop.
- The ecosystem read confirmed there is no existing owner for any of
  these: every execution skill (`color-grading-skill`,
  `motion-graphics-skill`, `thumbnail-skill`, `video-editing-skill`,
  `subtitle-skill`) does only narrow, creation-time self-verification of
  its own output; nothing in the ecosystem validates *across* skills'
  outputs today.
- This is additive to, not a replacement for, everything qc-skill already
  does. ADR-001 through ADR-008 stand unchanged: qc-skill still never
  produces a Decision, still keeps Measurement/Rule/Finding separate,
  still never hard-codes policy. The evolution is in *what* gets
  measured and checked (multiple related artifacts, and timing across an
  edit history) - not in who decides what to do about a `FAIL`.
- Explicit non-goals, restated because they are exactly what "cross-
  artifact" and "timeline-aware" QC could be mistaken for scope creep
  into: no subjective/LLM quality judgment of any kind; no editing
  algorithm implemented inside qc-skill (Phase 3 may only *compare*
  against an existing timeline record, never construct or reconstruct
  one); no image-recognition AI embedded directly (any future visual-
  layout finding must arrive via `media-analysis-skill`'s
  `Observation` -> `video-production-agent`'s QC specification, never a
  model call from inside qc-skill itself); no re-implementation of
  detection capability that already exists in `ffmpeg-skill`/
  `media-analysis-skill` - those are reused as measurement sources, not
  duplicated.

## ADR-010: multi-artifact delivery is a new kind (`delivery_package`), not an extension of the existing `delivery` kind

Phase 1 needs to validate N named artifacts (e.g. `final.mp4` + `ja.srt` +
`thumbnail.png` + `metadata.json`) as one delivery unit. Two ways to get
there: extend `DeliveryRule`/`kind: "delivery"` to accept an open-ended
artifact list, or add a new kind alongside it. Decision: **new kind**,
`kind: "delivery_package"`, with its own typed `DeliveryPackageRule` and a
`DeliveryArtifact` reference type (`artifact_id`, `artifact_type`, `path`,
`fingerprint`, `required`) - modeled on qc-skill's own existing dataclass/
rule conventions, not on any other repo's naming.

Reasons, in order of weight:

1. **Backward compatibility with a contract another skill has already
   pinned.** `video-production-agent`'s adapter
   (`tools/qc/adapter.py`) checks compatibility against a frozen
   `contract_0.1.0.json` snapshot via a `"0.1."` version-prefix gate.
   Changing what `kind: "delivery"` *means* - even additively - risks that
   existing, already-shipped integration in a way a new, separate kind
   cannot: old callers of `kind: "delivery"` get exactly the same checks,
   measurements and rule shape they always have, forever.
2. **No naming precedent to follow or collide with.** The ecosystem read
   confirmed no "delivery package" concept exists anywhere else in this
   ecosystem for `delivery_package` to conflict with, and no existing
   multi-artifact convention it should instead match.
3. **`DeliveryRule` is single-primary-artifact-plus-optional-companions by
   design** (`video`/`audio`/`subtitle` sub-rules, `require_video`/
   `require_audio`/`require_subtitle` flags) - that shape does not extend
   cleanly to an open-ended, caller-named list of arbitrary artifact
   types (e.g. a delivery with two subtitle languages, or a delivery with
   no video at all). A new rule type avoids bending the existing one past
   what it was designed to express.
4. **Keeps the qc-skill <-> agent seam typed and one-directional.**
   `DeliveryPackageRule`/`DeliveryArtifact` are the typed boundary object
   this kind accepts; qc-skill continues to never import
   `ProductionPlan`/`video_production_agent` types, and the agent-side
   adapter continues to translate its own richer model down to this
   shape, exactly as it already does for `kind: "delivery"`.

The existing `delivery` kind is not deprecated and is not planned to be:
it remains the right shape for "one video, optionally with one subtitle
companion," and `delivery_package` is additive for the N-artifact case,
not a replacement.

## ADR-011: cross-artifact validation is typed relationship rules over already-gathered measurements, never string comparison or LLM judgment

Phase 2 (`feature/cross-artifact-qc`) adds the first checks that compare
*different* artifacts in a `delivery_package` against each other -
duration consistency, and "if artifact A is present, artifact B must be
too." The evolution plan is explicit that this must never become simple
string comparison or LLM judgment; the design keeps that boundary by
construction:

- Both new rule types (`ArtifactDurationConsistencyRule`,
  `ArtifactDependencyRule`) are typed dataclasses under a new
  `CrossArtifactRule` (`rules.delivery_package.cross_artifact`), following
  the exact same "caller-supplied typed expectation, no hard-coded
  policy" shape as every other `Rule` in this skill (ADR-002, ADR-005).
  There is no free-text expression field anywhere in either rule - a
  duration rule names `artifact_ids` and a numeric `max_delta_sec`; a
  dependency rule names two artifact ids. Nothing here is evaluated as
  code (STEP 10's `eval`/`exec` ban was never at risk, but it is worth
  stating plainly: comparing typed fields is all `_evaluate_cross_artifact`
  does).
- The comparison itself operates on values already produced by the
  existing, unchanged per-artifact measurement functions
  (`container.duration_sec` for video/audio, `subtitle.duration_sec` for
  subtitle) - it does not re-read files, re-parse anything, or introduce
  a new measurement source. Cross-artifact validation is purely a second
  pass over data that was already OBSERVED.
- A duration comparison with a genuinely unmeasurable side is `UNKNOWN`,
  never a guessed `PASS` or a false `FAIL` - the same guard
  `_equality_check`/`_unknown_check` already apply everywhere else in
  this file (ADR guidance carried forward, not a new exception for this
  feature).
- This still is not, and must never become, a place to compare anything
  *semantic* (does the subtitle's wording match the video's content,
  does the thumbnail "look like" the video) - that would require exactly
  the AI/LLM judgment this skill is built to exclude (see `not_provided`
  in `contract.py`). Duration and presence are the only relationships
  implemented here because they are the only ones expressible as a typed
  numeric/boolean comparison over existing measurements; anything else
  stays out of scope until it can be expressed the same way.

## ADR-012: timeline integrity is a read-only TimelineMap *consumer*, never a competing editing model

Phase 3 (`feature/timeline-integrity-qc`) needs to check whether a
delivery's subtitle cue timing is still correct *after* a trim/concat/
speed edit, not just "correct relative to the source." The ecosystem read
(Step 0 of the evolution plan) confirmed there is no single reusable
general timeline object to adopt: `video_agent/temporal/timeline.py`'s
`TimelineMap` is dormant and exists only for multi-camera sync (unrelated
to edit history); the real general trim/concat/speed model is
`video-editing-skill`'s own `Segment`/`Clip`, captured by the agent's
adapter but never persisted downstream; and `agent/subtitles.py` has its
own narrow, subtitle-cue-only reimplementation of the same remapping.
qc-skill deliberately does not adopt, wrap, or reimplement any of these:

- `TimelineSegment` (`rules.py`) is a minimal, qc-skill-local read-only
  record - `source_start`, `source_end`, `delivery_start`, `speed` - that
  the caller (the agent, which already has the real edit history from
  whichever of the three sources above it uses) supplies as part of a
  `SubtitleRule.timeline_integrity` rule. qc-skill never constructs one,
  never infers cuts, never guesses which segments exist; it only maps a
  given source timestamp through segments it was handed
  (`_map_source_to_delivery`, a single pure arithmetic function - not an
  editing algorithm).
- The check compares the caller-supplied `source_cues` (the original,
  pre-edit cue timing the caller asserts existed) - mapped through the
  supplied `timeline` - against `subtitle.cues` (STEP: a new raw-cue-
  timing measurement added alongside the existing derived subtitle
  measurements, itself just parsed fact, no judgment). A source cue that
  falls entirely inside a cut region (no segment contains it) is
  `UNKNOWN`, not a guessed pass or fail, mirroring the freeze-segment
  precedent in `evaluate_video` (violations found -> `FAIL`; otherwise
  any unresolved case -> `UNKNOWN`; otherwise `PASS`).
- This is added as a `SubtitleRule` field, not a new top-level kind or a
  `delivery_package`-only feature, specifically so it is automatically
  available everywhere a `SubtitleRule` already nests today (standalone
  `kind: "subtitle"`, `kind: "delivery"`'s `subtitle` sub-rule, and
  `kind: "delivery_package"`'s per-artifact `subtitle` sub-rule) without
  new plumbing in `engine.py` at all - reuse over a parallel mechanism,
  consistent with how every other nested rule in this skill already
  works.
- What this explicitly does not do, by design: reconstruct a timeline
  from raw media (no scene-cut detection, no cross-correlation - that
  would be exactly the "editing algorithm inside qc-skill" the evolution
  plan forbids), or decide which of several conflicting timelines is
  correct (that is still the agent's `Decision`, unchanged since ADR-001).
  qc-skill only ever compares: a supplied `TimelineSegment` list, a
  supplied source-cue list, and observed delivery-timeline cue timing.

## ADR-013: luminance-range excursions via `signalstats` (deterministic pixel min/max), not `idet` (a probabilistic classifier)

Phase 4 (`feature/visual-defect-qc`) is the first of the "visual defect
evidence" additions the evolution plan describes (STEP: "do not
reimplement ffmpeg-skill/media-analysis-skill capability - reuse as
adapter/measurement; implement incrementally, not all at once").
Interlace detection (ffmpeg's `idet` filter) was tried first and
rejected based on direct measurement, not assumption:

- `idet` classifies each frame as TFF/BFF/Progressive/Undetermined using
  a field-difference heuristic. Tested against this repo's own
  `testsrc2`-based fixture pattern (the same generator `clean.mp4`/
  `black.mp4`/`freeze.mp4` already use), a genuinely progressive 25fps
  clip was classified **TFF: 42, Progressive: 8** (out of 50 frames) -
  a false-positive rate that would make a `video.interlacing_matches_expected`
  check actively wrong on ordinary progressive content, not just
  occasionally uncertain. A `tinterlace`-generated genuinely-interlaced
  clip was correctly classified (TFF: 25/25), so the filter is not
  useless - but its false-positive rate on common synthetic/high-motion
  progressive content is high enough that shipping a PASS/FAIL verdict
  on top of it would violate this skill's "never fabricate a measurement"
  standard (STEP 5/10) by dressing up a shaky heuristic as a confident
  verdict. This is recorded here specifically so a future session does
  not re-attempt the same approach without knowing why it was rejected.
- `signalstats` (`YMIN`/`YMAX` per frame) is not a classifier - it is a
  literal per-frame pixel value readout, verified against deliberately
  constructed illegal-range fixtures (`lutyuv=y=250` / `y=5`, both
  outside the legal 16-235 range for 8-bit limited-range video):
  `signalstats` reported `YMAX=250` / `YMIN=5` exactly, with zero
  ambiguity. This is the kind of measurement this skill can stand behind.

Design, following the same detection-vs-policy split as black/freeze
detection (ADR-005):

- **Detection parameters** (sensible universal default, not a policy
  choice): `luminance_legal_min`/`luminance_legal_max` (16/235 - the
  near-universal legal range for 8-bit limited-range Rec.601/709 video),
  used only to decide what counts as an "excursion" worth recording as a
  segment. This mirrors `black_pixel_threshold`/`freeze_noise_db`
  exactly: a detection default is fine because it does not assert
  anything is *unacceptable*, only what is *worth measuring*.
- **Measurement**: `video.luminance_excursions` - `[{start, end,
  duration, min_y, max_y}]`, built the same way `black_segments`/
  `freeze_segments` already are (contiguous runs of qualifying frames
  merged into segments, from the same single decode pass - ADR-006 still
  holds, `signalstats,metadata=print` is added to the existing filter
  chain, not a second decode). A raw per-frame list was rejected as a
  measurement shape - unlike black/freeze (typically sparse events), a
  multi-hour delivery could have per-frame data for every frame, an
  unbounded and mostly-uninformative payload; segments are exactly as
  informative and bounded the same way black/freeze already are.
- **Policy checks** (no default - "no opinion" until the caller says so,
  per ADR-005): `max_single_luminance_excursion_sec`/
  `max_total_luminance_excursion_sec` on `VideoRule`, evaluated only when
  set, exactly mirroring `max_single_black_sec`/`max_total_black_sec`.
  Whether *any* excursion is acceptable is a delivery-context decision
  (broadcast-safe vs. full-range web/streaming delivery legitimately
  differ) - qc-skill still never asserts "16-235 is required," only "here
  is where and how far outside 16-235 the signal went, if you care."

## ADR-014: `provides` groups checks into ten Capability ids, matching media-analysis-skill's collision pair

`contract.py` adds a top-level `provides` list for
`kajisho5/AI-video-production-OS`'s `CapabilityContract.provides`
(`docs/SPEC.md` there), so a registry can resolve "who provides
`measure.audio.loudness`" without hardcoding this repository. The ten ids
match those already assigned to this Skill's measurement groups in that
project's own `docs/CAPABILITY_MATRIX.md` section 8, kept here in
`contract.CAPABILITY_CHECK_GROUPS` as the single source of truth going
forward. Grouping is coarser than `SUPPORTED_CHECKS`: several related
checks judge one Capability (every `video.*_matches_expected` check is
`measure.video.format`).

Three of the ten ids - `measure.audio.loudness`, `measure.audio.silence`,
`measure.audio.integrity` - are the ecosystem's one documented Capability
collision (`CAPABILITY_MODEL.md`'s motivating example): media-analysis-skill
independently implements the same three measurements with no shared code
and publishes the identical id for each in its own `contract.py`, so a
registry sees one Capability with two Providers, not two unrelated things
that happen to share a name.

One check, `audio.sample_rate_matches_expected`, is deliberately in no
group (`contract.UNGROUPED_CHECKS`, enforced by
`tests/test_contract_completeness.py`): `CAPABILITY_MATRIX.md` assigns no
audio-format-shaped id today (only `measure.video.format` exists, and it
is explicitly scoped to video), so forcing this check into
`channel_layout` or `clipping_and_dynamics` would publish a capability
grouping this project has not actually decided on, rather than leave the
gap honest. This grouping predates Phase 1-4's new `delivery_package`/
`cross_artifact`/`timeline_integrity`/`video.luminance_*` checks
(ADR-010 through ADR-013): none of those new checks are in any Capability
group yet, for the same reason `audio.sample_rate_matches_expected` isn't
-- `CAPABILITY_MATRIX.md` has no assigned id for them today, so leaving
them out is the honest state, not an oversight (see
`tests/test_contract_completeness.py` for how ungrouped checks are kept
an explicit, enforced set rather than a silent gap). Additive: a new
top-level `provides` key, saying nothing `checks`/`measurements` don't
already say, only indexed by Capability id instead of check id.

## ADR-015: closing five measured-but-never-checked gaps (`kajisho5/qc-skill#7`)

A direct `ffmpeg-skill/scripts/check.py` comparison (prompted by
`kajisho5/AI-video-production-OS#36`, and confirmed a distinct,
deliberate design per ADR-001/004/005/007 - not a collision) surfaced
several values qc-skill already measured but never let a caller evaluate.
Items 1-4 and 6 of that issue were closed as pure `rules.py`/`contract.py`
additions, each following the exact shape every existing `Rule` field
already uses (ADR-002/005: caller-supplied, `None` = no opinion, no
hard-coded threshold):

- **Duration ceiling** - `VideoRule.max_duration_sec`, checked against the
  already-measured `container.duration_sec` (`video.duration_within_limit`,
  `VIDEO_DURATION_EXCEEDED`). `UNKNOWN` when duration could not be
  measured, exactly like every other policy check here.
- **Delivery size ceiling** - `DeliveryRule.max_size_bytes`, alongside the
  pre-existing `min_size_bytes` floor. Both now feed the *same*
  `delivery.file_size_within_limit` check (mirroring how
  `max_single_black_sec`/`max_total_black_sec` already share one check) -
  a size that is simultaneously below the floor and above the ceiling
  (a caller error, but not qc-skill's to reject) surfaces both
  `DELIVERY_FILE_TOO_SMALL` and `DELIVERY_FILE_TOO_LARGE` rather than
  picking one.
- **Colour/HDR** - four new `VideoRule` fields
  (`expected_color_range`/`color_space`/`color_transfer`/`color_primaries`),
  each a plain exact-equality check against the measurement of the same
  name (`video.color_range`, etc. - already measured, per `contract.py`,
  since before this issue) - the identical mechanism `expected_codec`/
  `expected_pixel_format` already use. The common case the issue names -
  "flag HDR delivered to an SDR-only platform" - falls out of this for
  free: `expected_color_transfer: "bt709"` FAILs against an HDR source
  (`smpte2084`/PQ or `arib-std-b67`/HLG), with no HDR-specific
  branching or classifier added.
- **VFR detection** - a new measurement,
  `video.variable_frame_rate_suspected`, computed the same way
  ffmpeg-skill's own `variable_frame_rate_suspected` is:
  `|r_frame_rate - avg_frame_rate| > 0.01` fps (a *detection* threshold,
  ADR-005 - it decides what counts as suspicious timing, not whether VFR
  is acceptable). Unlike ffmpeg-skill, which defaults to `False` when a
  rate is missing, the measurement is `None` here when either rate
  couldn't be read - qc-skill never guesses "not VFR" the way it never
  guesses any other absent fact. `VideoRule.disallow_vfr` (`None` = no
  opinion) gates the corresponding `video.frame_rate_is_constant` check.
- **Resolution "at least"** - `VideoRule.min_width`/`min_height`,
  evaluated by a second, independent check
  (`video.resolution_meets_minimum`, `VIDEO_RESOLUTION_BELOW_MINIMUM`)
  alongside - not replacing - the pre-existing exact-equality
  `video.resolution_matches_expected`. A caller can set either, both, or
  neither; the two checks never interact.

Item 5 of that issue (embedded/muxed subtitle-stream checks - `probe.py`
per-track `subtitle_stream_details`, not just standalone SRT/VTT/ASS
files) was explicitly scoped out: it needs a new probe-style measurement
path (`measurements/subtitle.py` only ever reads a caption file from
disk), not a `Rule` field, and is a larger, separate change.

## ADR-016: photosensitive flashes follow ITU-R BT.1702-3, with the display mapping stated as an assumption

Inspiration (ideas only, no code taken): `win4r/video-director`'s
`video-director/scripts/qa_video.py` (MIT) checks a render for flashes,
planned cuts and on-screen text size/contrast. qc-skill takes the three
*questions* and answers each against a named standard or a caller's own
data, in this repository's measurement/rule/finding idiom.

A flash check is only worth shipping if it follows a published
definition, not a home-made "frame differs from its neighbours"
heuristic. The thresholds were taken from the primary source, read
directly (2026-10-30):

- ITU-R BT.1702-3 (11/2023), *Guidance for the reduction of
  photosensitive epileptic seizures caused by television*,
  https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1702-3-202311-I!!PDF-E.pdf
  - Guideline 1: a potentially harmful flash is a pair of opposing
    luminance changes; when the darker image is below 160 cd/m2, a
    change is a difference of 20 cd/m2 or more (SDR and HDR); when the
    darker image is not below 160 cd/m2, a Michelson contrast above 1/17
    applies, **for HDR programmes only**. A sequence is not permitted
    when *both* the concurrently flashing area exceeds **25% of the
    displayed screen area** *and* there are **more than three flashes
    (i.e. six changes) within any one-second period**. Flashes whose
    leading edges are >= 360 ms (50 Hz) / 334 ms (60 Hz) apart are
    acceptable irrespective of brightness or area.
  - "Irrespective of luminance, a transition to or from a saturated red
    is also potentially harmful" - but no colorimetric definition is
    given, and Annex 3 says "more explicit definitions and detection
    criteria will also be required in specifying guidance on the use of
    saturated red colour".
  - Note 3 / Annex 2 / Table 1: SDR is assumed displayed with peak white
    = 200 cd/m2 (HLG 1 000, PQ 10 000); Table 1's SDR column is
    `200 * V^2.4` (BT.1886, zero black level; e.g. 10-bit code 400 ->
    20.06 cd/m2, code 863 -> 160.4 cd/m2), `V = (D - 64) / 876` for
    10-bit, i.e. `(Y - 16) / 219` for 8-bit.
- The 2005 edition (BT.1702-0,
  https://www.itu.int/dms_pubrec/itu-r/rec/bt/R-REC-BT.1702-0-200502-I!!PDF-E.pdf)
  has the same 20 cd/m2 / 160 cd/m2 / 25% / three-flash core.
- Ofcom's guidance (the legacy ITC note it publishes,
  https://www.ofcom.org.uk/__data/assets/pdf_file/0021/16248/gn_flash.pdf)
  uses the same pair of conditions (more than one quarter of the
  displayed screen area and more than three flashes within any
  one-second period), which originate in the Harding/Binnie work
  BT.1702 cites; WCAG 2.x SC 2.3.1
  (https://www.w3.org/WAI/WCAG20/Understanding/three-flashes-or-below-threshold)
  cites BT.1702 for the same general-flash definition.

Decisions:

- **Opt-in** (`VideoRule.check_photosensitivity: true`): the analysis
  needs per-frame, per-area luminance, which the existing single pass
  (whose stdout already carries `signalstats` metadata) cannot expose
  without changing every existing request's pass. One extra decode, only
  when asked; ADR-006 still holds for every request that does not ask.
- **Display mapping** is BT.1702's own SDR assumption above, stated in
  the measurement's `notes`, not hidden. HDR transfer (PQ/HLG) is
  `null`/UNKNOWN: the HLG/PQ curves and the HDR-only Michelson branch are
  not implemented, and qc-skill does not guess.
- **Area**: luma is area-averaged by ffmpeg onto a 32x32 grid (each cell
  1/1024 of the screen); "more than 25%" is evaluated strictly. The
  25% is a *detection* parameter (`flash_area_fraction`, ADR-005: it
  defines what counts as a screen flash); `max_flashes_per_second`
  (default 3, the standard's number) is the policy rule. Averaging code
  values rather than light is exact for uniform cells and an
  approximation at edges - documented, not hidden.
- **Counting**: a per-cell peak/trough tracker records a change once
  luminance has moved >= 20 cd/m2 back from the running extreme with the
  darker side < 160 cd/m2; `flashes = changes / 2`, so 7 changes (3.5
  flashes) already exceeds "more than three". The 360/334 ms clause is
  a consequence of the same limit, not separately evaluated.
- **Red flash**: not implemented; `video.red_flash_count` is always
  `null` with the reason, and the check evidence says
  `red_flash: UNKNOWN`. It is deliberately *not* a separate check whose
  status is always UNKNOWN - under worst-wins aggregation (ADR-004) that
  would turn every report that asks for flash analysis UNKNOWN. A
  caller that needs a red-flash verdict has none from qc-skill and
  should say so.
- Fixtures (`tests/fixtures/generate.py`): full-screen 10 Hz white/black
  = FAIL (10 flashes), 2 Hz = PASS, a 9%-area 10 Hz flash = PASS, a
  50%-area 10 Hz flash = FAIL, a ~1 cd/m2 swing = no change, a 179 vs
  200 cd/m2 swing (darker side >= 160) = no SDR flash.

## ADR-017: planned-cut accuracy compares scdet cut times against the caller's cut list

video-production-agent renders from an IR that already knows where every
cut is; this check lets it verify the render against that list without
qc-skill ever constructing the list itself (the ADR-012 stance: consume
the caller's plan, never compete with it).

- **Measurement**: `video.scene_cuts` from ffmpeg `scdet` (threshold
  `scene_cut_threshold`, default 10 = scdet's own default, a detection
  parameter), read from `metadata=print` frame lines - frame index and
  pts time of the first frame of each new shot. Runs in the same extra
  decode as ADR-016 when both are requested; only when
  `VideoRule.expected_cuts` is set.
- **Rule**: `expected_cuts` (typed list of non-negative seconds, sorted
  on parse), `max_cut_error_frames` (default 1), `allow_extra_cuts`
  (default true), `cut_match_window_sec` (default 0.5).
- **Matching** is a deterministic nearest-unmatched match per expected
  cut in time order, within the window; the check evidence reports
  `matched` (with `error_frames = |detected - expected| * fps`),
  `missing`, `extra` and `max_error_frames`. `VIDEO_CUT_MISMATCH` when
  anything is missing, the max error exceeds the limit, or an extra cut
  exists while `allow_extra_cuts` is false.
- scdet is itself a detector with a threshold: a soft cut or a cut
  between near-identical shots can go undetected (reported as `missing`,
  a fact about the detector at that threshold, which the caller can
  lower), and a flash or a whip-pan can register as an extra cut (hence
  `allow_extra_cuts` defaults to true).

## ADR-018: text legibility is contrast and ink height at caller-named regions, never OCR

`VideoRule.text_regions: [{t, box: [x, y, w, h], role}]` (`role` in
`hook|cta|caption|term`) names where the caller put text. For each,
qc-skill decodes one frame at `t`, crops the box, and measures two
facts in pure Python:

- **Contrast**: Otsu's threshold on gamma-encoded luma splits the box
  into two classes (minority = ink); contrast ratio is the WCAG 2.x
  definition `(L1 + 0.05) / (L2 + 0.05)` on the two classes' *median*
  relative luminance (sRGB-linearised, 0.2126/0.7152/0.0722), per
  https://www.w3.org/TR/WCAG22/#dfn-contrast-ratio and
  https://www.w3.org/TR/WCAG22/#dfn-relative-luminance. Medians, not
  means, so anti-aliased glyph edges do not drag the ink class toward
  the background. `min_contrast_ratio` defaults to 4.5 (WCAG 2.x AA,
  SC 1.4.3, normal text); 7.0 is AAA (SC 1.4.6), 3.0 the AA large-text
  figure - the caller chooses.
- **Height**: the tallest run of pixel rows containing ink. For
  all-caps text without descenders this is the cap height; for
  mixed-case text it includes ascenders/descenders and is an upper bound
  on cap height. Reported raw and scaled to a 1080 px short side.
  `min_text_height_px` is per role and has **no default**: no standard
  gives a pixel floor per role, so qc-skill holds no opinion until the
  caller supplies one (ADR-005).

`TEXT_ILLEGIBLE` when any region is below a minimum; a region that
cannot be measured (box outside the frame, no frame at `t`) is
`measured: false` with a reason, and the check is UNKNOWN unless another
region already failed. What the text *says* is never read - OCR and
semantic judgement stay out of scope (`not_provided`).

None of the three new checks joins a `provides` Capability group:
`CAPABILITY_MATRIX.md` assigns no id for photosensitivity, cut-list
accuracy or text legibility (`measure.video.scene_detection` is
media-analysis-skill's raw detector, a different capability), so they
are listed in `contract.UNGROUPED_CHECKS` like the ADR-014 precedent.

## ADR-019: `request.timeout` is one wall-clock budget for the whole request; running out kills the child's process group and fails with `TIMEOUT`

`request.timeout` was parsed and validated but never used: the only
limits were the fixed per-invocation ceilings in `probe.py` and
`measurements/*` (60/120 s, `TOOL_ERROR` when they run out), so a caller
could not bound a request, and a caller that killed `qc` itself left any
running ffmpeg/ffprobe behind.

- **One budget, not one per child.** `runner.request_deadline(timeout)`
  starts the clock (the CLI opens it before capability detection;
  `run_report` opens it again for library callers - a nested budget never
  extends the enclosing one). It is checked when `run_report` starts,
  before every child is launched, and once more before the response is
  returned. Every child gets `min(its own ceiling, time left)`.
- **Killing.** Children already run in their own process group
  (`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP` on
  Windows). When the budget runs out mid-child, the whole group is killed
  - `os.killpg(pid, SIGKILL)` on POSIX, `taskkill /T /F /PID` (from
  `%SYSTEMROOT%\System32`) on Windows, which walks the process tree -
  followed by a direct `kill()` of the child, and the child is reaped
  before `TIMEOUT` is raised, so nothing qc started outlives the call.
  SIGKILL, not SIGTERM first: ffmpeg/ffprobe here only read and write to
  `-f null` or a pipe, so there is no partial output to flush. Same
  pattern as hyperframes-skill `lib/proc.mjs` and transcription-skill
  ADR-003.
- **Error.** A new code `TIMEOUT`, exit code 15, `retryable: true`, in
  the existing `qc/response@1` failure envelope; `details` carries
  `timeout` (the budget), `stage` (the executable or check point that ran
  out) and, when a child was killed, its `seconds`. A per-invocation
  ceiling that runs out first is still `TOOL_ERROR` - that is the tool
  hanging, not the caller's budget. `CANCELLED` stays reserved for an
  outside cancellation.
- **What is not undone.** A report that finished computing after the
  budget ran out is still written to the cache (it is a correct report,
  and a retry then returns it at once), but the response is `TIMEOUT`.
  `timeout` is not part of the report identity: it changes whether a
  report is returned, never what it says.

## ADR-020: every reported version is read from pyproject.toml, never from a constant in the code

`qc_skill.VERSION` was a hard-coded `"0.1.0"` while the release workflow
(`.github/workflows/release.yml` -> `scripts/release_version.py set`)
bumps only `pyproject.toml`, so after v0.2.0/v0.2.1 `contract --json`,
the contract's tool specs, `doctor --json` and every report's
`skill_version` still said `0.1.0`.

- **One source: `pyproject.toml`.** `qc_skill._version()` reads the
  `version` line of this checkout's `pyproject.toml` (only if its `name`
  is `qc-skill`) when running from source - which includes an editable
  install, whose recorded distribution metadata goes stale on the next
  release bump - and otherwise `importlib.metadata.version("qc-skill")`,
  i.e. the metadata of the installed wheel, built from that same file. If
  neither is found it reports `0.0.0+unknown` rather than a plausible but
  wrong number. Same rule as media-analysis-skill and subtitle-skill.
- **Not chosen: have the release workflow also bump the constant.** That
  keeps two copies of the version and relies on a second regex write
  staying in step with the first; one copy cannot drift.
- **Guarded by a test.** `tests/test_version_source.py` asserts that
  `VERSION`, `skill_contract()["version"]`, `tool_spec(...)["version"]`,
  `doctor_report()["skill"]["version"]` and the CLI's `contract --json` /
  `doctor --json` all equal the version in `pyproject.toml`.
- **Effect on callers.** qc now reports its real release version
  (`0.2.x` at the time of writing). A caller that gates on the `"0.1."`
  prefix (`video-production-agent`'s `tools/qc/adapter.py`, see ADR-010)
  will refuse it until that caller accepts `0.2.`; the contract itself is
  unchanged (`contract_version` stays `"1"`). `skill_version` is part of a
  report's identity, so reports cached under the old `0.1.0` label are
  recomputed rather than reused.

## ADR-021: full-reference metrics (VMAF/SSIM/PSNR) are opt-in, compare against `request.reference_video`, refuse misaligned pairs, and are UNKNOWN without libvmaf

Roadmap step 3 (`ECOSYSTEM_ROADMAP.md`); the Phase 5 gap in
`docs/qc-evolution-gap-analysis.md`. A render can only be scored against
its master if the caller says which master - so the whole feature is
opt-in, and every default request behaves exactly as before.

- **Opt-in and where the reference comes from.** `VideoRule.reference_metrics`
  (a list of `"vmaf"`, `"ssim"`, `"psnr"`) switches it on, for
  `kind: video` and for `kind: delivery`'s `rules.delivery.video`. The
  reference file is the existing top-level `reference_video` key (until
  now used only by `kind: subtitle`), not a path inside the rule: paths
  stay out of rules (`rules.py`), and the top-level key already goes
  through `PathPolicy.resolve_input`, is fingerprinted into the report
  identity and shows in `provenance.reference_video_input`. For
  `kind: video`/`delivery` it is read **only** when the rule opts in, so
  a request that sent `reference_video` before keeps the same identity and
  report. `kind: delivery_package` has no per-artifact reference path; a
  `reference_metrics` rule there is UNKNOWN ("no request.reference_video").
  The new rule fields are a `List[str]` and three `float`s - within the
  typed-rule grammar `video-production-agent`'s qc adapter accepts
  (`List[scalar|NamedRule]`, `Dict[str, scalar]`, scalars).
- **Computation.** One extra ffmpeg run with both files as inputs and one
  internally built `-filter_complex` (fixed filter names, ffprobe
  integers, an allow-listed pixel format - never caller text): each side
  `setpts=PTS-STARTPTS`, the pair split into `libvmaf`, `ssim`, `psnr`,
  each with `shortest=1`, output to `-f null`. Scores are the filters'
  own summary lines (libvmaf "VMAF score", ssim "All", psnr "average").
  libvmaf runs with its default model (vmaf_v0.6.1 in libvmaf 2.x); the
  model is not selectable, because a model path would be a caller-supplied
  file the filter loads.
- **Size mismatch: scale, by a documented rule.** The VMAF FAQ
  (https://github.com/Netflix/vmaf/blob/master/resource/doc/faq.md, read
  2026-10-30): with a 1080 reference and a 480 encode, "the right way to
  do it is to upsample the 480 video to 1080, and calculate the VMAF at
  1080, together with its 1080 source". qc-skill scales the distorted
  picture to the reference's size (bicubic) for all three metrics and
  records `scaled_distorted_to`. Pixel formats are unified to the
  reference's when libvmaf accepts it (planar YUV 4:2:0/4:2:2/4:4:4,
  8/10 bit), else `yuv420p`.
- **Frame-rate / length mismatch: refuse, structured.** These metrics pair
  frame N with frame N; at different rates or with a trimmed clip they
  measure the misalignment, not the encode, and there is no rule-free way
  to retime or find an offset. So frame rates must agree within 0.01 fps
  (the tolerance `video.variable_frame_rate_suspected` already uses) and
  frame counts within one frame (durations within one frame when a count
  is missing). Otherwise `video.reference_alignment` is
  `{status: "refused", code, reason, distorted, reference}` with `code` in
  `FRAME_RATE_MISMATCH | FRAME_COUNT_MISMATCH | DURATION_MISMATCH |
  FRAME_RATE_UNKNOWN | LENGTH_UNKNOWN | REFERENCE_NO_VIDEO |
  DISTORTED_NO_VIDEO`, the scores are `null`, and each requested check is
  UNKNOWN with that code in its `reason` and the record in its evidence.
  *Not chosen: a request-level error.* An `INVALID_INPUT` failure would
  throw away every other check in the same report (format, black frames,
  loudness) for a problem that concerns one opt-in comparison, would turn a
  property of the media into an execution failure (README: errors are for
  execution problems), and would add nothing a caller cannot read from the
  structured record; ADR-004 already guarantees an UNKNOWN check keeps the
  report from aggregating to PASS.
- **libvmaf availability.** `capabilities.detect_capabilities` matches
  exact filter names in `ffmpeg -filters` (a substring test would take
  `libvmaf_cuda` for `libvmaf`) and reads `ffmpeg -buildconf` for
  `--enable-libvmaf`. `doctor --json` reports `filter:libvmaf` (with
  `buildconf_enable_libvmaf`), `filter:ssim`, `filter:psnr` as optional:
  their absence lists the matching check under `unavailable_tools` but
  never makes doctor `degraded`. Without libvmaf, `"vmaf"` is not run and
  `video.vmaf_meets_minimum` is UNKNOWN with the reason; SSIM and PSNR
  still run. A libvmaf that is listed but fails at run time (e.g. a build
  without its model) is retried once without it, so it never takes SSIM
  and PSNR down with it.
- **Infinite PSNR.** A bit-identical pair has infinite PSNR, which the
  canonical JSON used for identity/cache (`allow_nan=False`) cannot carry.
  It is reported as libvmaf's own PSNR ceiling for the bit depth (60 dB at
  8 bit, 72 dB at 10 bit - libvmaf `psnr_max`), with `psnr_capped: true`.
  Finite values are reported as ffmpeg prints them.
- **Thresholds.** These are policy, and ADR-005 says policy has no default;
  as with `max_flashes_per_second` (ADR-016) and `min_contrast_ratio`
  (ADR-018), the defaults below apply only once the caller has opted in,
  and each comes from a published source rather than from qc-skill:
  - `min_vmaf = 93`: R. Rassool, "VMAF Reproducibility: Validating a
    Perceptual Practical Video Quality Metric", RealNetworks, 2017
    (https://realnetworks.com/sites/default/files/vmaf_reproducibility_ieee.pdf,
    read 2026-10-30), conclusion (c): "if a video service operator were to
    encode video to achieve a VMAF score of about 93 then they would be
    confident of optimally serving the vast majority of their audience
    with content that is either indistinguishable from original or with
    noticeable but not annoying distortion". (Netflix's own FAQ gives no
    threshold and warns that absolute scores are not comparable across
    resolutions; Netflix has described ~6 VMAF points as one just
    noticeable difference.)
  - `min_ssim = 0.95` and `min_psnr_db = 31`: the lower bound of MOS 4
    ("Good") in A.-N. Moldovan, I. Ghergulescu, C. H. Muntean, "A Novel
    Methodology for Mapping Objective Video Quality Metrics to the
    Subjective MOS Scale", IEEE BMSB 2014, Table I
    (https://norma.ncirl.ie/1298/1/Moldovan_et_al._-_2014_-_A_Novel_Methodology_for_Mapping_Objective_Video_Qu.pdf,
    read 2026-10-30), which tabulates the
    SSIM mapping of T. Zinner et al., "Towards QoE Management for Scalable
    Video Streaming", ITC Specialist Seminar 2010 (>= 0.99 excellent,
    >= 0.95 good, >= 0.88 fair) and the PSNR mapping of C.-H. Ke et al.,
    "An Evaluation Framework for More Realistic Simulations of MPEG Video
    Transmission", JISE 24(2), 2008 (> 37 dB excellent, 31-37 good, 25-31
    fair). Zinner et al.'s own PSNR mapping puts "good" at 33 dB - the
    caller who prefers it sets `min_psnr_db: 33`.
- **Capability grouping.** `CAPABILITY_MATRIX.md` assigns no id for
  full-reference quality metrics, so the three checks are in
  `contract.UNGROUPED_CHECKS` (ADR-014 precedent).
- **Tests and CI.** `tests/test_reference_metrics.py` generates a master,
  a crushed copy (boxblur + crf 51), a half-size copy, a 30 fps clip and a
  truncated clip with ffmpeg: identical = SSIM 1.0 / PSNR 60 (capped) /
  VMAF >= 93 PASS, crushed = FAIL at the defaults and PASS at lowered
  thresholds, half size = scaled and scored, 30 fps and truncated =
  refused/UNKNOWN. The missing-libvmaf path is tested by simulation
  everywhere, and for real on any runner whose ffmpeg lacks libvmaf; the
  real VMAF scores are tested where the runner's ffmpeg has libvmaf, and
  `QC_REQUIRE_LIBVMAF=1` turns a skip into a failure on the CI job(s)
  known to have it.

## ADR-022: `provides[]` names per-operation tool ids (`qc/check`, `qc/inspect`); `qc/run` stays as an alias

`provides[]` (ADR-014) gave every Capability one tool id, `qc/run`, the name
of the CLI subcommand. video-production-agent never used that id: its
adapter (`tools/qc/adapter.py`, its ADR-032) registers `qc/check` and
`qc/inspect` and translates each to the request's `operation` field, and
its production SkillSpec `qc_check` references `qc/check`. Its provides
diagnostic (`video_agent/skills/diagnostics.py`) joins on tool-id string,
so all ten Capabilities came back `PROVIDES_MISMATCH` and `qc/check` came
back `UNKNOWN` ("not published under provides[]"). AI-video-production-OS
`DECISION_LOG.md` (D9) had read this as expected granularity and decided
"no fix"; its later ecosystem roadmap (this repository's
`ECOSYSTEM_ROADMAP.md` step 2) asks for the fix here instead, because the
agent's split is the one consumers actually address, and only the Skill
can publish ids every consumer can join on.

- **Contract `tools`.** A new top-level list names one tool per
  operation: `qc/check` (operations `check` and `validate`, which README
  documents as the same pipeline), `qc/inspect` (`inspect`), and
  `qc/run` with `alias: true` (every operation: it is the subcommand).
  Each non-alias operation maps to exactly one tool
  (`tests/test_contract_completeness.py`).
- **`provides[]` entry shape.** `tool_id` is now `qc/check`: every entry
  is a group of *checks*, and only `check` evaluates them. `tool_ids`
  lists every per-operation tool that serves the Capability
  (`["qc/check", "qc/inspect"]`: inspect returns the same measurements
  without a verdict). `alias_tool_ids: ["qc/run"]` keeps the old id
  resolvable for any consumer that pinned it. `tool_id` stays a single
  string because that is the `{id, tool_id, lifecycle}` shape every
  consumer already reads; the two lists are additive.
- **Not chosen: one provides[] entry per (Capability, tool) pair.** It
  would publish each Capability id twice; registries key on the id
  (AI-video-production-OS `registry/`), and the agent's diagnostic would
  then also report `qc/inspect` as `CAPABILITY_UNCONSUMED` ten times
  while no SkillSpec calls it.
- **No request change.** `qc run` still takes `operation` in the request;
  the tool ids are names for consumers to join on, not new commands.
- **Result.** Run against the agent's own `check_provides()` (read-only),
  the live contract goes from 10 `PROVIDES_MISMATCH` + 1 `UNKNOWN` to 10
  `PROVIDES_VALID`. `provides` and `tools` are not in the agent's pinned
  drift keys, so its availability gate is unaffected, but its pinned
  `contract_*.json` snapshot must be re-pinned to carry the new fields.

## ADR-023: subtitle cue text and subtitle-vs-speech checks are opt-in, read a caller-supplied transcript, and never guess

Subtitle QC so far checked structure and timing against itself or against
caller-supplied numbers. Whether a cue sits on the speech it shows needs
the speech's own timing, which qc-skill does not measure (transcription
is out of scope, `not_provided`). The transcript a pipeline already has
(transcription-skill's word-timed JSON) is the fact to compare against.

- **Input.** `SubtitleRule.transcript` is a path, resolved through the
  path policy like `input`/`subtitle`/`reference_video`, and fingerprinted
  by content. The fingerprint joins `asset_fingerprints`
  (`transcript:<sha256>`, or `<artifact_id>:transcript:<sha256>` in a
  delivery package) and the path is replaced in the rules payload, so the
  identity follows the transcript's bytes and stays free of machine-local
  paths (contract `identity.excludes`). It is a rule field rather than a
  top-level request key because it only matters to the subtitle rule that
  names it, and a delivery package can name one per subtitle artifact.
- **Tolerant reading.** transcription-skill 0.9.0 adds a contiguous
  whole-transcript `index` on every word, and, on dictionary-segmented
  words only, a coarse `pos` and `timing: "measured" | "interpolated"`;
  older files have none of them. The reader takes `text` or
  `word`, keeps `index` when it is an integer and always derives
  `position` (reading order over every word entry, so the two agree when
  both exist), reads `pos` only when present, and skips (and counts)
  words without text or usable timing. Nothing is repaired or inferred.
- **Matching.** Time: a word belongs to the cue on screen at its
  midpoint. Text: both sides in NFKC, case-folded, with punctuation,
  symbols, spaces and control characters removed; a cue is aligned only
  when its words spell its text exactly. Fuzzy alignment was not chosen:
  a near-match decides which word starts a line, and a wrong guess there
  turns into a confident FAIL or PASS.
- **Checks.** `subtitle.cue_boundaries_on_word_boundaries`
  (`word_boundary_tolerance_sec`), `subtitle.cue_not_early`
  (`max_cue_lead_sec`), `subtitle.speech_covered`
  (`max_uncovered_speech_sec`, `extra_covered_intervals` for time that an
  emphasis graphic or similar already covers),
  `subtitle.no_forbidden_line_start` (`forbidden_line_start_pos`,
  `forbidden_line_start_phrases`, each line of each cue),
  `subtitle.total_chars_within_limit` (`max_total_chars`, FAIL) and
  `subtitle.cue_chars_meet_minimum` (`min_cue_chars`, WARN: a one-word
  reply is a legitimate cue, so a short cue is a prompt to look, not a
  defect). Each runs only when its field is set (ADR-005): none of these
  thresholds is a standard, so none has a default; docs/checks.md lists
  starting points instead.
- **UNKNOWN, not a guess.** No transcript, an unreadable one, or no `pos`
  makes the dependent check (or the pos part) UNKNOWN with a reason. The
  pos part is never derived from characters: a leading `が` can begin a
  noun. A violation that rests on an interpolated word timing is listed
  as unconfirmed and gives UNKNOWN rather than FAIL. A phrase is matched
  literally, but when the cue is aligned it must also end on a word
  boundary, so a one-character phrase does not match inside a longer
  word.
- **Characters.** The skill had no width-aware counting (`max_line_length`
  counts raw code points), so the new limits count NFKC code points of
  the visible text (markup removed, line breaks not counted). Display
  width (full-width = 2) was not chosen: subtitle character limits are
  normally stated in characters, and NFKC already folds half-width
  katakana and full-width Latin into one count.
- **Evidence.** One finding per check, as elsewhere, with
  `evidence.violations` sorted by time; each entry has `time_sec`, the
  cue's `cue_index`/`cue_id`/`text`, the words involved, and a
  one-line `description`, so the list is both machine-readable and
  readable as "seconds: what is wrong".
- **Language.** The default assumes no language. `preset: "ja"` is an
  explicit opt-in that fills only unset fields with suggested Japanese
  values (pos `particle`/`auxiliary`, a short phrase list, 32/4
  characters); it sets no timing threshold, because those do not depend
  on the language.

The six checks join no `provides` Capability group: CAPABILITY_MATRIX.md
assigns no id for them yet, so they are in `contract.UNGROUPED_CHECKS`
(ADR-014 precedent).

## ADR-024: a stinger's cut point is checked by its alpha at that frame, and a delivered cut by the frame it shows

video-editing-skill's stinger transition (its ADR-023/ADR-032) switches
clips at the stinger's cut point, the frame where the graphic covers the
whole picture, and refuses a stinger whose frame there is not opaque.
Its roadmap step 13 and hyperframes-skill's step 15 name a qc-skill
stinger cut-point check as an acceptance point, on the stinger and on
the edited result. qc-skill had none.

- **Rule.** `VideoRule.stinger: {cut_point, cover?, file?, cut_at?,
  max_cut_frame_difference?}`. `cut_point` and `cover` are the stinger
  maker's statement (stinger.json); the caller passes them, as it does to
  video-editing-skill, and qc-skill opens no sidecar. Without `file` the
  input is the stinger. With `file` the input is a delivered video: the
  stinger is a second input, resolved and fingerprinted by content like
  `SubtitleRule.transcript` (ADR-023; `stinger:<sha256>` in the asset
  fingerprints, the path replaced in the rules payload), and `cut_at`
  names the cut times on the delivered timeline. A rule field rather
  than a top-level key because it only matters to the video rule that
  names it, and a delivery package can name one per video artifact.
- **The frame.** `round(cut_point * frame_rate)` with the exact
  `r_frame_rate` fraction; a time more than 1/1000 of a frame from a
  frame is UNKNOWN (video-editing-skill refuses it the same way). The
  frame is decoded after an input seek to a quarter frame before it, and
  the decoded frame's timestamp must name that frame - a seek that lands
  elsewhere is UNKNOWN with the time it landed on, never a neighbouring
  frame measured in its place.
- **Opaque rule.** The same as video-editing-skill's: every pixel's
  alpha at least 253 of 255 (2 of 255 of tolerance), compared at the
  plane's native bit depth as `alpha * 255 >= 253 * max` (65021 = 253 *
  257 at 16 bit, as there). The alpha plane comes from ffmpeg
  `extractplanes=a` at its own depth (`gray`, `gray10le`, `gray12le`,
  ...), read in Python; no rescaling step stands between the decoder and
  the comparison. It is a fixed rule, not a rule field: the point is to
  agree with the editor that cut there.
- **No alpha is UNKNOWN.** Whether a pixel format has alpha comes from
  ffprobe's own pixel-format flags (`-show_pixel_formats`), checked
  before anything is decoded: some filters would add an opaque alpha to
  a stream without one, and that must never read as "covered".
- **Delivered cuts.** At a cut under full cover the delivered frame is
  the stinger's frame, so the check compares the delivered frame at
  each `cut_at` with the stinger's frame at `cut_point` (scaled to the
  delivered size): mean absolute difference of 8-bit RGB.
  `max_cut_frame_difference` has no default, because the difference left
  by a correct cut is the delivery's encoding loss, which only the caller
  knows; docs/checks.md gives measured numbers to start from.
  Neighbouring frames are reported as context, not judged: a stinger
  that holds full cover for several frames makes them equal, and that is
  not a defect.
- **Not `video.cuts_match_expected`.** Reusing ADR-017's scdet matching
  was considered first and measured: on the test edit (one join at 2.0
  s) scdet reports 1.5, 1.933, 2.0, 2.1 and 2.7 s - the graphic's own
  steps - and a stinger placed one frame late shifts each by a frame,
  inside the default tolerance; on a smooth wipe it reports no cut at
  all. A switch under full cover is invisible by design, so a
  scene-change detector cannot locate it.
- **Three checks**, so the three ways a stinger cut goes wrong are told
  apart: `video.stinger_cut_point_in_cover` (the statement contradicts
  itself: `VIDEO_STINGER_CUT_POINT_OUTSIDE_COVER`),
  `video.stinger_opaque_at_cut_point` (a hole in the graphic, or a
  stinger.json naming the wrong frame: `VIDEO_STINGER_NOT_OPAQUE`) and
  `video.stinger_cut_frame_matches` (the stinger placed off the cut:
  `VIDEO_STINGER_CUT_FRAME_MISMATCH`). Each finding carries the time in
  seconds and the measured values. An unreadable stinger file, a frame
  that cannot be decoded and a stream without alpha are UNKNOWN with the
  reason; a missing stinger file is `MISSING_INPUT`, as a missing
  transcript is.

What is not checked: only the cut frame, not every frame of the stated
cover window (that frame is where the clips switch, as in
video-editing-skill), and not which clip shows before and after the cut
(qc-skill is not given the clips). The checks join no `provides`
Capability group (ADR-014 precedent).

## ADR-025: a loop seam is measured for a click, a level step and a change of spectral shape, from the decoded samples, with no default limit

sound-design-skill makes loopable beds (its roadmap step 7, ADR-011);
its acceptance point is a bed repeated 10 times passing qc-skill's loop
seam check, which did not exist. A loop is heard end -> start, so the
seam to judge is the wrap, and a file made of repeats has seams inside it
as well.

- **Rule.** `AudioRule.loop_seam: {window_sec?, seams?, include_wrap
  (default true), max_click_ratio?, max_level_diff_db?,
  max_band_diff_db?}`. Declaring the file loopable is the rule's
  presence. Each limit runs its own check only when set (ADR-005): none is
  a standard, so none has a default, and docs/checks.md gives measured
  starting points instead. `window_sec` has no default either: the level
  and spectrum limits need it, and a request that sets one without it is
  refused rather than given a guessed window.
- **Samples, not filters.** One ffmpeg decode to 32-bit float at the
  file's own rate and channel count (`ffmpeg_rawaudio_argv`), then pure
  Python. ffmpeg's `astats`/`ebur128` report a whole file or fixed
  windows, not two windows aligned on a seam, and the wrap only exists
  once the end and the start are joined, which is done in memory, never
  written. ffmpeg is told to stop writing after 128 MiB of samples
  (`-fs`), so a file whose duration is missing or wrong cannot fill the
  memory; such a file is UNKNOWN. The samples are read in place, not
  copied. 128 MiB is about six minutes of 48 kHz stereo, far above a
  bed's length.
- **Click.** A discontinuity is a step or a kink; the second difference
  `x[i-1] - 2x[i] + x[i+1]` spikes at both and stays small on smooth
  material. The ratio of its largest value within 1 ms of the seam to
  its 99th percentile in the 50 ms around (outside that 1 ms) is
  dimensionless, so one limit serves quiet and loud beds: continuous
  material measures about 1. The percentile, not the maximum, keeps a
  drum hit near the seam from hiding a click. Out of digital silence the
  ratio is undefined; a silent seam is 0 and anything else is reported as
  a click out of silence, never as a number made up to fit.
- **Level.** RMS dBFS of `window_sec` on each side (floored at -120 so
  silence stays a number in JSON). Unweighted RMS, not a K-weighted
  short-term loudness: the question is whether the two sides match, and
  both sides go through the same measure.
- **Spectrum.** Octave bands (62.5 Hz * 2^k; the top one cut off at
  the Nyquist frequency, so nothing above the last full octave is left
  out) of the same windows, each
  relative to its side's total, so this check sees the shape and leaves a
  plain level step to the level check: one cause, one finding. Bands
  more than 30 dB under the loudest are not compared (they are inaudible
  next to it and their ratios are noise). The frames are the whole
  frames next to the seam, Hann-windowed; the transform is a radix-2 FFT
  in Python (no numpy).
- **Channels.** Click, level and spectrum are measured on every channel
  and the worst is reported with its index. Mixing to mono first was
  tried and dropped: channels out of phase on one side of a seam cancel
  there in the mix, and a seam whose channels each matched measured as a
  109 dB spectral "mismatch".
- **No audio stream.** Every limit the rule sets is reported UNKNOWN
  with that reason, not dropped.
- **Seams that do not fit** (a file shorter than two windows, a seam
  within a window of an end or past it, less than 50 ms of audio around
  it for the click) are not measured for that fact, with the reason;
  nothing is padded. A check is UNKNOWN when no seam fails and one could
  not be measured.
- **Evidence.** One finding per check; `evidence.violations` sorted by
  time with `seam`, `time_sec`, the measured values and a one-line
  `description`; the finding's `timestamp` is the first violation's time.
  Values are rounded once when measured and judged as reported, so the
  limit's edge is exact for a caller reading the report.

Also with this change: a NaN or an Infinity anywhere in a request (which
`json.loads` accepts) is `INVALID_REQUEST` before any field is read. It
used to reach a rule as a limit that every value fails (NaN) or passes
(Infinity), or the identity hash as an uncaught error.

The three checks join no `provides` Capability group (ADR-014
precedent).

## ADR-026: a video loop's seam is its last -> first frame step against the median step inside the loop next to it, with no default limit

The title, stinger and loop material hyperframes-skill renders was
checked on its seam only by the renderer itself; qc-skill's loop-seam
check (ADR-025) is for audio. A loop is seen end -> start, so the step to
judge is the loop's last frame to its first.

- **Rule.** `VideoRule.loop_seam: {window_frames?, segments?,
  max_seam_ratio?}`. Declaring the file a loop is the rule's presence.
  `max_seam_ratio` has no default (ADR-005: none is a standard); without
  it the seam is measured and reported, not judged. `window_frames`
  (1-12, default 4) is a measuring window, not a limit, so it has one;
  12 keeps the frames held in memory to 26.
- **Segments.** With title.json's `segments: {in, loop, out}` the loop is
  `in <= t < in + loop` of the full clip, so the clip motion-graphics-skill
  places can be checked without the separate `.loop` file. Both ends must
  be on a frame (the 1/1000-of-a-frame rule of ADR-024); `out` is taken
  as stated and not compared with the clip's length. A `loop` of 0 is
  refused: a title without a loop has no seam.
- **Relative, not absolute.** The seam's mean |RGB| (0-255) is divided
  by the median of the consecutive-frame steps in the window on both
  sides, so one limit serves a slow drift and a fast move: continuous
  motion measures about 1 and a loop one frame short about 2 (it shows
  two frames' worth of motion at once). The median, not the mean or the
  maximum, keeps one cut or flash inside the window from setting the
  reference. On a still picture the ratio is undefined; a still seam is 0
  and any step out of a still picture fails, never a number made up to
  fit (as ADR-025's click out of silence).
- **Frames.** The two windows are decoded with one ffmpeg run each, by
  an input seek to a quarter frame before, every frame's timestamp
  checked (ADR-024's rule, now shared in `measurements/frames.py`). The
  file's last frame is the packet count's; one frame more is asked for,
  and if it comes the file is UNKNOWN rather than judged at a wrong end.
  RGB at the stream's own size, not luma or a scaled copy: a colour-only
  jump or a one-pixel detail would vanish in either.
- **Evidence.** The seam's frame pair and times, the ratio, the
  reference and every inside step; the finding's `timestamp` is the
  loop's last frame.

## ADR-027: alpha edges are checked at the declared mode: premultiplied constrains the pixels, and both modes are composited over black and white against the graphic's own colour

A lower third or stinger declared straight but delivered premultiplied
(or the reverse) shows a dark or light outline in every edit it is
placed in. title.json / stinger.json state the mode (`alpha`); nothing
checked it.

- **Rule.** `VideoRule.alpha_edge: {alpha_mode, times?,
  max_fringe_px?}`. `alpha_mode` is required: it is the material's
  statement and is never guessed from the pixels. `times` picks the
  frames; by default 5 spread from the first to the last (the edges of a
  title exist in most of its frames, and every frame of a long clip in
  pure Python would be slow). `max_fringe_px` has no default.
- **Mode check.** Premultiplied means no colour above its alpha (2/255
  of rounding allowed). Straight allows any colour at any alpha, so the
  check passes for it once the frames are read; a premultiplied file
  declared straight cannot be told from its pixels alone (a dark graphic
  is both) and is caught by the fringe check instead.
- **Fringe.** A halo is an edge pixel that composites darker or lighter
  than the graphic would at that alpha. The graphic's colour is not
  known, so it is carried in from the nearest opaque pixels ring by ring
  (at most 8 px deep), and each partly transparent pixel is composited
  over black and over white with the declared formula against that
  colour at the same alpha. Over black, a straight file read as
  premultiplied brightens by `c * (1 - a)`; over white, premultiplied
  read as straight darkens; both backgrounds are computed so each error
  shows where it is visible. 16/255 marks a fringe pixel: the right mode
  measured at most 1.1/255 on the test material (rounding of ffmpeg's
  premultiply) and the wrong one 21-57/255 across the ramp. A fixed
  constant, like ADR-024's 253/255, not a rule field: the caller's
  judgment is the width.
- **Width.** For each fringe pixel, the shorter of its horizontal and
  vertical runs: a band along a vertical edge measures its thickness, not
  the edge's length, and a shallow diagonal edge is not counted along its
  rows. The frame's width is the largest.
- **Not judged.** Partly transparent pixels with no opaque pixel within
  8 px (a glow or a soft shadow alone) have no graphic colour to compare
  with; a frame with only those is UNKNOWN for the fringe. A soft effect
  of another colour drawn against opaque fill reads as a fringe; that is
  documented, not hidden.
- **No alpha** (by ffprobe's pixel-format flags) is UNKNOWN for both
  checks, as for the stinger.

## ADR-028: a key / fill pair is matched on size, rate, frame count and timing first, then frame by frame on the key against the fill's alpha or the fill's luma outside the key

A key one frame late, or a fill that leaks outside the key, shows as a
flash or an outline on a broadcast switcher. ffmpeg-skill's
`export --preset key-fill` checks its own pair when it writes it; a pair
from anywhere else, or after a transfer, had no check.

- **Rule.** `VideoRule.key_fill: {key_file, tolerance?,
  max_off_fraction?, key_range?}`; the input is the fill. The key is a
  second input like the stinger file (ADR-024): resolved through the path
  policy, fingerprinted by content (`key:<sha256>`, path replaced in the
  rules payload), `provenance.key_input`.
- **Pair first.** Width, height, exact frame rate, frame count (ffprobe
  packets, demuxed without decoding), start time (half a frame) and the
  timecode tag must match before a pixel is decoded: a frame-count
  mismatch makes every later comparison meaningless, and the roadmap
  asks for it to fail first.
- **Frames.** Every frame, not a sample: a key off by one frame on a
  short move shows only on those frames. Chunks of at most 32 MiB per
  file keep memory bounded on long material; each frame's timestamp is
  checked. With an alpha in the fill, the key must equal it within
  `tolerance` (default 2/255, ffmpeg-skill's own `key_level`
  tolerance). Without one, the fill must be black where the key is: its
  luma at most `tolerance` wherever the key is at most `tolerance`.
  Luma, because a 4:2:2 fill's chroma reaches past every vertical edge by
  construction (4% of the moving-box pair's pixels by RGB, none by
  luma). `max_off_fraction` (default 0) allows isolated codec outliers
  when a caller needs to; the evidence gives the first differing frame,
  its share of pixels off and how many frames differ.
- **Key range.** A key is a luma matte. Its stream's `color_range` says
  how to read it; unknown is read as limited (video black transparent,
  video white opaque), the broadcast convention ffmpeg-skill's
  documentation states. ffmpeg's `alphaextract,format=yuv422p10le`
  writes the alpha at full range into a stream that carries no range
  tag (measured with ffmpeg 6.1): a binary alpha reads the same either
  way, a soft one does not. `key_range` overrides the reading so such a
  key can be checked as what it holds; the default stays limited because
  that is how a switcher reads an untagged key.

The four checks of ADR-026/027/028 join no `provides` Capability group
(ADR-014 precedent).
