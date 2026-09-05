# Testing

```bash
pip install -e .
pip install pytest
pytest -q
```

A real `ffmpeg`/`ffprobe` on `PATH` is required - the test suite fails
loudly at session start (`tests/conftest.py`) rather than skipping if they
are missing. Nothing binary is committed to the repository; every fixture
is generated fresh from a documented `ffmpeg -f lavfi` invocation
(`tests/fixtures/generate.py`), so expected measurement values are known
exactly (e.g. `clean.mp4` is defined as 320x240 @25fps, 100 frames, mono
440Hz tone).

## Fixtures (`tests/fixtures/generate.py`)

| fixture | what it exercises |
|---|---|
| `clean.mp4` | normal video+audio, no defects - the baseline-passes case |
| `video_no_audio.mp4` | missing audio stream |
| `black.mp4` | one ~1.5s black segment |
| `freeze.mp4` | 2s motion + ~2s frozen frame |
| `corrupted.mp4` | `clean.mp4` with zeroed bytes stamped into compressed frame data - real decode errors |
| `silence_gap.wav` | 2s tone / 2s silence / 2s tone - internal (unexpected) silence |
| `loud_clipping.wav` | sine driven well past 0 dBFS - real digital clipping |
| `subtitle_valid.srt` | well-formed cues aligned to `clean.mp4`'s duration |
| `subtitle_mismatch.srt` | ends far short of the reference video's duration |
| `subtitle_malformed.srt` | backwards timestamp, duplicate id, empty cue |

## Test matrix

- **Unit** (`test_models.py`, `test_security.py`, `test_schemas.py`) -
  `QCStatus` aggregation, `PathPolicy`, request validation - no ffmpeg
  needed.
- **Real-media E2E** (`test_video_checks.py`, `test_audio_checks.py`,
  `test_subtitle_checks.py`, `test_delivery_checks.py`) - every
  measurement and check family, run through the real engine against real
  fixtures: inspect, check (pass and fail cases), video/audio/subtitle/
  delivery, and QC-failure scenarios (black frame, freeze, clipping,
  corrupted decode, loudness out of range, missing audio, wrong
  resolution/fps, subtitle mismatch) verified as *findings*, distinct from
  execution errors.
- **Contract/doctor** (`test_contract_doctor.py`) - the contract is valid
  JSON, only advertises implemented operations/checks/measurements, and
  `doctor` correctly reports `AVAILABLE`/`MISSING` for each dependency and
  filter.
- **Cache/reuse** (`test_cache_reuse.py`) - a second identical request is
  reused; a tampered cache entry is rejected as invalid, not returned as a
  hit; `cache_policy: only` without an entry raises `VALIDATION_ERROR`.
- **Provenance/determinism** (`test_provenance_determinism.py`) - identity
  is stable across workspaces/clocks and changes when content or rules
  change; `request_id` never affects identity; `observed_at` is UTC
  ISO-8601 with a `Z` suffix.
- **CLI E2E** (`test_cli_e2e.py`) - `contract`/`doctor`/`run` as actual
  subprocesses, stdin and file-argument request delivery, malformed-JSON
  handling, and the execution-vs-QC-status distinction (`status:
  "completed"` with `overall_status: "FAIL"` exits 0).
- **Security** (`test_security.py`, `test_security_injection.py`) -
  forbidden request keys (including nested), shell-metacharacter
  filenames, path traversal, prefix collision, symlink escape (input and
  output), and Windows-reserved filenames.

## Cross-platform

The suite is designed to pass on Linux, macOS, and Windows CI
(`.github/workflows/ci.yml` runs all three): symlink-specific tests are
skipped on Windows (`sys.platform == "win32"`), traversal detection splits
on both `/` and `\` regardless of host OS, and `check_filename` tests
(reserved device names, trailing dot/space, reserved characters) run
everywhere since they exercise pure string logic, not the filesystem.
