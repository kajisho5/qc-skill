# qc-skill

Deterministic media quality control / validation. `qc-skill` measures and
checks video, audio, subtitle, and delivery artifacts - a single file, or
(`kind: "delivery_package"`) N named artifacts (video + subtitle +
thumbnail + metadata, ...) validated together as one delivery - and
reports structured `PASS` / `WARN` / `FAIL` / `UNKNOWN` results with the
evidence behind each one.

**`qc-skill` is not an AI agent and does not make production decisions.**
It never decides whether a video may ship, whether it should be
re-rendered, or whether a defect should be fixed. It answers exactly one
question - "what did we measure, and did it pass the rules we were given?"
- and returns that as data. Turning a QC result into a decision (block
delivery, re-render, ask a human) belongs to `video-production-agent`.

## Ecosystem and responsibilities

| Skill | Responsibility |
|---|---|
| `ffmpeg-skill` | low-level ffmpeg/ffprobe execution primitives |
| `media-analysis-skill` | general-purpose media observation/analysis |
| `audio-production-skill` | audio processing **execution** (gain, trim, normalize, mix, ...) |
| `video-editing-skill` / `subtitle-skill` | editing and subtitle **execution** |
| **`qc-skill`** | **measurement + rule evaluation + structured verdicts. Never decides, never edits.** |
| `video-production-agent` | turns Observations (including QC reports) into Decisions and a ProductionPlan |

A QC `FAIL` is not an error. Running `qc check` against a broken file and
getting back `{"status": "completed", ..., "overall_status": "FAIL"}` is
qc-skill working correctly - the *execution* succeeded, and the *finding*
is that the media does not meet the rule it was checked against. Only a
genuine execution problem (bad request, missing ffmpeg, a file that
doesn't exist, a corrupted cache) is a structured *error* with a non-zero
exit code. See [docs/architecture.md](docs/architecture.md).

## CLI usage

```bash
qc contract --json          # what this skill can do
qc doctor --json            # is ffmpeg/ffprobe actually available right now
qc run request.json --json  # or: qc run - --json < request.json
```

A request document:

```json
{
  "operation": "check",
  "kind": "video",
  "input": "/workspace/output/final.mp4",
  "rules": {
    "video": {
      "expected_width": 1920,
      "expected_height": 1080,
      "expected_frame_rate": 29.97,
      "max_single_black_sec": 2.0
    }
  }
}
```

`--workspace` and `--allowed-input-root` are CLI flags, supplied by
whoever invokes the process - never by the request body itself (see
[docs/security.md](docs/security.md)).

## What it returns

```json
{
  "schema": "qc/response@1",
  "status": "completed",
  "skill": {"id": "qc", "version": "0.1.0"},
  "reused": false,
  "cache": {"status": "miss", "policy": "use", "key": "..."},
  "provenance": {
    "skill": "qc", "skill_version": "0.1.0", "operation": "check",
    "engine": {"ffmpeg_version": "6.1.1", "ffprobe_version": "6.1.1"},
    "input": {"fingerprint": "sha256:...", "size_bytes": 12345678},
    "identity": "...", "observed_at": "2026-01-01T00:00:00Z",
    "measurement_source": "OBSERVED"
  },
  "report": {
    "id": "qcreport_...", "version": "1", "operation": "check", "kind": "video",
    "overall_status": "FAIL",
    "checks": [
      {"check_id": "video.resolution_matches_expected", "category": "video", "status": "PASS", "measurement_ids": ["video.width", "video.height"], "finding_codes": [], "evidence": {}},
      {"check_id": "video.black_frames_within_tolerance", "category": "video", "status": "FAIL", "measurement_ids": ["video.black_segments"], "finding_codes": ["VIDEO_BLACK_FRAMES_EXCEEDED"], "evidence": {"total_sec": 3.2, "longest_sec": 2.1}}
    ],
    "measurements": [
      {"id": "video.width", "category": "video", "name": "width", "value": 1920, "unit": "px", "source": "ffprobe", "estimated": false}
    ],
    "findings": [
      {"code": "VIDEO_BLACK_FRAMES_EXCEEDED", "severity": "FAIL", "message": "longest black segment 2.1s exceeds 2.0s", "evidence": {"...": "..."}, "measurement_ids": ["video.black_segments"]}
    ]
  }
}
```

## Operations

- **`inspect`** - measure only. Returns `measurements`; `checks` and
  `findings` are empty and `overall_status` is `UNKNOWN` (nothing was
  evaluated, which is different from everything having passed).
- **`check`** - measure, then evaluate every rule the caller supplied plus
  a small set of always-on baseline checks (decode integrity, clipping,
  structurally malformed subtitle cues - see
  [docs/checks.md](docs/checks.md)).
- **`validate`** - the same pipeline as `check`; the name exists for
  callers that want to express "this is the final delivery gate" in their
  own request, typically with `kind: "delivery"` and a full expected spec.

## Kinds, measurements, checks

See [docs/checks.md](docs/checks.md) for the full, current list of
measurements/checks/findings per kind
(video/audio/subtitle/delivery/delivery_package).
`qc contract --json` is the authoritative, machine-readable version of the
same list - the contract only ever advertises what is actually
implemented.

## Measurement vs. Rule vs. Finding

```
Measurement  = a fact obtained from the media (never a judgment)
Rule         = a caller-supplied, typed expectation (e.g. target LUFS + tolerance)
Finding      = what happened when a Rule was evaluated against a Measurement
```

`qc-skill` never hard-codes a policy like "YouTube requires -14 LUFS" -
targets and tolerances are always explicit, typed request parameters (see
`VideoRule` / `AudioRule` / `SubtitleRule` / `DeliveryRule` in
`src/qc_skill/rules.py`). Rule evaluation is a fixed set of Python
comparisons - there is no expression language, no `eval`, no way to send a
rule that executes arbitrary logic.

## PASS / WARN / FAIL / UNKNOWN

`UNKNOWN` means the check could not be performed - a required measurement
was unavailable, a dependency (ffmpeg) was missing, or a value needed for
comparison (e.g. a reference video duration) wasn't supplied. `UNKNOWN` is
never collapsed into `PASS`, and it is never conflated with `FAIL`: a
`QCCheck` with status `UNKNOWN` always carries a `reason` explaining why it
could not run. Aggregation is worst-wins: `FAIL` > `UNKNOWN` > `WARN` >
`PASS` (see `QCStatus.aggregate` in `src/qc_skill/models.py`).

## Security

- No `shell=True`, ever. Every subprocess call is an argv list built
  entirely from fixed flags plus one resolved, validated path.
- The request schema rejects `command`, `argv`, `args`, `shell`, `cmd`,
  `exec`, `executable`, `filter`, `filter_complex`, `env` outright, at any
  nesting depth - a caller cannot smuggle a command or a raw filtergraph
  through the request.
- `PathPolicy` confines input reads to an optional allow-list of roots and
  output/report writes to a workspace, resolving symlinks before every
  containment check.

See [docs/security.md](docs/security.md) for the full boundary and what is
deliberately *not* enforced (e.g. resource/memory limits).

## Provenance and reuse

Every report records `skill`, `skill_version`, `operation`, the actual
`ffmpeg`/`ffprobe` versions used, the input's content fingerprint
(`sha256`, never path/mtime), an `identity` hash, and an `observed_at`
timestamp. `measurement_source` is always `"OBSERVED"` - qc-skill never
fabricates a measurement it could not take.

Reports can be safely reused: `identity = sha256(canonical_json({asset
fingerprint, kind, operation, effective parameters, rules, ffmpeg/ffprobe
versions}))`. A cached report is re-validated against its own stored hash
before being returned as a hit; a corrupted or hand-edited cache entry is
never returned as a successful reuse. See
[docs/architecture.md](docs/architecture.md#reuse--cache).

## Development

```bash
pip install -e .
pip install pytest
pytest -q            # requires a real ffmpeg/ffprobe on PATH
```

See [docs/testing.md](docs/testing.md) for the test matrix and how
fixtures are generated.

## Documentation

- [docs/architecture.md](docs/architecture.md) - pipeline, responsibility
  boundary, provenance, reuse
- [docs/security.md](docs/security.md) - PathPolicy, subprocess boundary,
  forbidden request keys
- [docs/checks.md](docs/checks.md) - measurements / rules / findings /
  checks catalog
- [docs/decisions.md](docs/decisions.md) - key design decisions (ADRs)
- [docs/testing.md](docs/testing.md) - test matrix and fixtures
- [docs/qc-evolution-gap-analysis.md](docs/qc-evolution-gap-analysis.md) -
  competitor/ecosystem research behind the multi-skill-pipeline QC
  evolution (delivery gate, cross-artifact, timeline integrity)

## License

MIT
