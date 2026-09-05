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
