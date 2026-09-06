# Architecture

## Responsibility boundary

```
video-production-agent                      qc-skill
------------------------                     --------
Observation                                  input validation (PathPolicy)
Event / Context                              media inspection (ffprobe)
Inference                                     measurement (ffprobe + ffmpeg filters)
Policy / Preference / Constraint              rule evaluation (typed Rule -> QCCheck/QCFinding)
Decision                        <---QCReport---  structured QCReport
ProductionPlan                               provenance
Project IR
Compiler
Skill (invokes qc-skill, among others)
```

`qc-skill` produces a `QCReport`; it never produces an `Observation`,
`Inference`, `Decision`, or `ProductionPlan` object itself (those are
`video-production-agent` concepts). A `QCReport` is *input* to the
agent's own `Observation`/`Decision` pipeline - typically wrapped as one
`Observation` whose `data` is the report, with `source =
"qc/<kind>@<version>"`.

## Pipeline

```
request
  -> PathPolicy.resolve_input()          # STEP 15/16: confinement, traversal/symlink checks
  -> probe_media() (ffprobe)             # STEP 3: structured container/stream facts, never raw stdout
  -> measurements/{video,audio,subtitle} # STEP 3-7: typed QCMeasurement objects
  -> rules.evaluate_*()                  # STEP 9/10: typed Rule + Measurement -> QCCheck + QCFinding
  -> QCReport                            # STEP 1: checks + measurements + findings + overall_status
  -> provenance + cache                  # STEP 14/21
  -> response envelope                   # STEP 18
```

Everything downstream of `probe_media`/`analyze_video_defects`/
`analyze_audio` consumes typed data, never raw ffprobe JSON or ffmpeg
stderr text - see `src/qc_skill/probe.py` and
`src/qc_skill/measurements/`.

## Two-tier check model

Every `evaluate_*` function in `src/qc_skill/rules.py` produces two kinds
of `QCCheck`:

- **Baseline checks** - run unconditionally during `check`/`validate`
  because they test an objective invariant, not a policy: decode
  integrity (`video.decodes_without_errors`, `audio.decodes_without_errors`),
  clipping (`audio.no_clipping`), and structurally malformed subtitle cues
  (invalid timestamps, duplicate ids, empty cues, control characters).
  These do not depend on any `Rule` field being set.
- **Policy checks** - run only when the caller supplies the relevant
  `Rule` field (expected resolution, loudness target, silence tolerance,
  line-length limit, ...). A field left `None` means "no opinion"; the
  corresponding check is simply not produced. `qc-skill` never guesses a
  default for a judgment call (STEP 5/10 of the task spec).

## Execution vs. QC status

`response.status` ("completed" | "failed") describes whether the *QC run
itself* succeeded. `report.overall_status` (PASS/WARN/FAIL/UNKNOWN)
describes what was *found*. A completed run with `overall_status: "FAIL"`
is the normal, correct outcome of checking a bad file - it is not an
error and must not be treated as one by a caller.

```
execution: {"status": "completed"}       <- the QC skill ran successfully
QC:        {"overall_status": "FAIL"}    <- the media failed the rules given
```

## Provenance

Recorded on every report (`provenance` object, top-level and echoed inside
`QCReport.provenance`):

- `skill`, `skill_version`
- `operation`
- `engine.ffmpeg_version`, `engine.ffprobe_version` - the actual binaries
  used, not just "ffmpeg is installed"
- `input.fingerprint` (sha256 of file *content*), `input.size_bytes`
- `identity` - see below
- `observed_at` - UTC, second precision, `Z` suffix
- `measurement_source: "OBSERVED"` - qc-skill never fabricates a
  measurement; a value it could not obtain is `null` with a `notes` field
  explaining why, or the owning check is `UNKNOWN`.

## Determinism

```
identity = sha256(canonical_json({
  skill, skill_version, kind, operation,
  asset_fingerprints,          # sha256 of file content, never path/mtime
  effective_parameters,        # detection defaults merged with request overrides
  rules,                       # the exact typed rule payload evaluated
  ffmpeg_version, ffprobe_version,
}))
```

Excluded from identity by construction: timestamps, machine-local paths,
and the caller-supplied `request_id` label. `canonical_json` is
`json.dumps(sort_keys=True, separators=(",", ":"), allow_nan=False)`
(`src/qc_skill/canonical.py`). Two runs of the same input against the same
rules on the same skill/engine versions always produce the same
`report.id` and `provenance.identity`, regardless of when or where they
run (see `tests/test_provenance_determinism.py`).

## Reuse / cache

`QCReportCache` (`src/qc_skill/cache.py`) stores one JSON file per
identity hash, sharded by the first two hex characters. A read is a hit
only if the stored metadata (skill version, fingerprints, kind, operation,
effective parameters, engine versions) matches the current request *and*
the stored report's hash matches a freshly recomputed `stable_hash` of its
own content - a corrupted or hand-edited cache file is deleted and treated
as a miss, never returned as a successful reuse.

`cache_policy`:
- `use` (default) - read on hit, write on miss.
- `bypass` - never read or write the cache for this call.
- `only` - read only; raises `VALIDATION_ERROR` if there is no cached
  entry (useful for a caller that wants to assert "this was already
  checked").

## Delivery package (`kind: "delivery_package"`)

A delivery is often more than one file - a video plus a subtitle plus a
thumbnail plus a metadata sidecar - produced independently by several
skills. `kind: "delivery_package"` validates N *named* artifacts as one
unit, without qc-skill ever importing a `ProductionPlan`/agent-side type
directly (ADR-010, `docs/decisions.md`):

```
request.artifacts:            [{artifact_id, artifact_type, path}, ...]   # what exists (typed, request-side)
rules.delivery_package.artifacts: [DeliveryArtifactRule, ...]              # what's expected of each (typed, rule-side)
```

The two lists are paired by `artifact_id`, mirroring how `request.subtitle`
is already separate from `DeliveryRule` today. `fingerprint` is never a
caller-supplied field - qc-skill always computes it itself
(`sha256_file`) from the resolved file, the same stance ADR-008 already
takes for cache reuse.

A required artifact that is genuinely absent is a normal, reportable
`FAIL` (`DELIVERY_PACKAGE_ARTIFACT_MISSING`), not a request-level error:
`PathPolicy.resolve_input(path, must_exist=False)` still enforces every
other boundary (traversal, control characters, not-a-regular-file,
outside the allowed input roots) exactly as it does for every other kind
- only "the file does not exist" is downgraded from an exception to a
measurement, and only for this kind.

Because several artifacts in one report can produce a measurement with
the same id (two subtitle artifacts both have `subtitle.cue_count`),
`QCMeasurement`/`QCCheck`/`QCFinding` all carry an optional `artifact_id`
field (`None` for every other kind) to disambiguate them.

Per-artifact checks (Phase 1) ask, for one artifact at a time: is it
present when required, the right size/extension, and (if a nested
`video`/`audio`/`subtitle` rule was given) does that one artifact pass
those checks on its own - never compared against anything else in the
package.

**Cross-artifact validation** (`rules.delivery_package.cross_artifact`,
Phase 2, ADR-011) compares *different* artifacts against each other:
duration consistency (`ArtifactDurationConsistencyRule`) and
presence dependency (`ArtifactDependencyRule`). Both are typed
relationship rules over measurements the per-artifact gathering already
produced - never a string comparison, never LLM/semantic judgment
("does the subtitle's wording match the video" is out of scope
everywhere in qc-skill, not just here). See `docs/checks.md` for the
full check/finding list.

**Still explicitly out of scope**: timeline-aware validation (does a
delivery's subtitle cue timing survive a trim/concat/speed edit) is
Phase 3 (`docs/qc-evolution-gap-analysis.md`), a distinct, not-yet-built
capability.

## Relationship to other skills

- **`ffmpeg-skill` / `media-analysis-skill`** - general-purpose
  probing/analysis. `qc-skill` uses the same engines (ffmpeg/ffprobe) but
  is the dedicated, rule-driven *validation* domain: it always produces a
  PASS/WARN/FAIL/UNKNOWN verdict against an explicit spec, which those
  skills' own `check.py`/analyzers do not attempt to standardize across
  video/audio/subtitle/delivery in one contract.
- **`video-editing-skill` / `audio-production-skill` / `subtitle-skill`** -
  execution skills. They *act* on media; `qc-skill` only ever *reads* it.
- **`video-production-agent`** - the only place a QC finding becomes a
  `Decision`. `qc-skill` is designed to be called once per artifact/kind
  and to return everything the agent needs to reason about the result
  without qc-skill itself weighing in on what to do.
