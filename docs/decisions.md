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
