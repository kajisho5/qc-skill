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
| `flash_10hz.mp4`, `flash_2hz.mp4` | full-screen white/black flashing at 10 Hz (BT.1702 FAIL) / 2 Hz (PASS), 20 fps |
| `flash_small_area.mp4`, `flash_half_area.mp4` | 10 Hz flash over 9% (PASS) / 50% (FAIL) of the screen |
| `flash_dim.mp4`, `flash_bright_only.mp4` | ~1 cd/m2 swing (no change) / darker side ~179 cd/m2 (no SDR flash) |
| `cuts.mp4` | four flat shots, cuts at frames 25/60/90 of 25 fps (1.0/2.4/3.6 s) |
| `text_boxes.mp4` | 640x360 drawbox "glyph" bars: white 40 px, #2A2A2A 40 px (low contrast), white 6 px |
| `ref_master.mp4` | 320x240 @25fps, 2 s, crf 1 - the reference for VMAF/SSIM/PSNR (and, compared with itself, the identical pair) |
| `ref_degraded.mp4`, `ref_small.mp4` | the master blurred + crf 51 (below every default threshold) / at 160x120 (compared after upscaling) |
| `ref_30fps.mp4`, `ref_short.mp4` | 30 fps / first 25 frames only - refused alignments (UNKNOWN) |
| `stinger_wipe.mov`, `stinger_prores.mov` | 64x36 @30fps, 1.2 s stinger with alpha (QuickTime Animation 8-bit / ProRes 4444): left half covered for frames 0-12, three quarters for 13-14, the whole picture for 15-17 (cut point 0.5 s), right half after |
| `stinger_edge.mov` | frame 10 every alpha exactly 253 (opaque by the rule), frame 20 one pixel at 252 (not) |
| `stinger_edit.mp4`, `stinger_edit_late.mp4` | red 2 s + blue 2 s joined at frame 60 (2.0 s), `stinger_wipe` laid over the join with its frame 15 on frame 60 / one frame late |
| `vloop_orbit.mp4`, `vloop_pan.mp4` (+ `_short`) | 64x36 @30fps loops: a soft dot once round an ellipse in 30 frames / a 32 px periodic texture panned 1 px a frame for 32 frames; `_short` drops the last frame |
| `vloop_title.mp4`, `vloop_one_frame.mp4`, `vloop_undecodable.mp4` | IN 10 + LOOP 30 (the orbit) + OUT 10 frames / a single frame / the orbit with its picture data zeroed (probes, decodes nothing) |
| `alpha_straight.mov`, `alpha_premultiplied.mov`, `alpha_straight_prores.mov`, `alpha_glow.mov` | grey 230 with alpha ramping over x = 16..22, straight / premultiplied (QuickTime Animation) / straight ProRes 4444; alpha 128 everywhere (nothing opaque) |
| `kf_source.mov`, `kf_fill.mov`, `kf_key.mov` | a 230/200/60 box moving 1 px a frame with alpha (ProRes 4444) and the fill + key ffmpeg-skill's `key-fill` chain writes from it (ProRes 422 HQ) |
| `kf_key_late.mov`, `kf_key_small.mov`, `kf_key_short.mov`, `kf_key_tc.mov`, `kf_lit_fill.mov`/`kf_lit_key.mov`, `kf_soft_key.mov` | the key a frame behind / 64x34 / 29 frames / with a timecode tag; a pair with one pixel lit under alpha 0 on frame 10; a soft alpha ramp's key (full range, untagged) |
| `loop_seamless.wav`, `loop_naive.wav` | 16 kHz mono 250 + 1000 Hz: 2 s, whole cycles (continuous wrap) / cut at 2.003 s (a jump at the wrap) |
| `loop_repeat10.wav`, `loop_naive_repeat10.wav` | each of the two repeated 10 times, sample for sample (seams every 2 s / 2.003 s) |
| `loop_level_step.wav`, `loop_spectrum_step.wav` | 4 s beds, continuous at the wrap, quieter / brighter (2000 Hz instead of 250 Hz) before it than after it |
| `loop_silence.wav`, `loop_click_from_silence.wav`, `loop_stereo_click.wav`, `loop_empty.wav` | digital silence; silence whose last sample is half scale; stereo with the right channel's phase flipped at 1 s; a WAV header with no samples |

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
- **Timeout** (`test_timeout.py`) - `request.timeout: 0.01` fails with
  `TIMEOUT` (exit 15) through the library and the CLI and leaves no child
  or process group behind; a running ffmpeg and a child's own grandchild
  are killed when the budget runs out (ADR-019).
- **Reference metrics** (`test_reference_metrics.py`) - VMAF/SSIM/PSNR
  (ADR-021): identical PASS, degraded FAIL at the defaults, size mismatch
  scaled, frame-rate/length mismatch refused, libvmaf missing (simulated
  everywhere, real where the host ffmpeg lacks it) -> VMAF UNKNOWN with
  SSIM/PSNR still scored. The real VMAF tests skip when the host ffmpeg
  has no libvmaf, unless `QC_REQUIRE_LIBVMAF=1` (set on the CI job whose
  ffmpeg has it), which makes them fail instead.
- **Subtitle vs. speech** (`test_subtitle_speech_checks.py`) - ADR-023:
  each of the six checks passes, fails, sits on its tolerance edge and
  turns UNKNOWN without a transcript (or without `pos`, or with an
  interpolated timing); transcripts with and without `index`/`pos`/
  `text`/`word`, skipped words, unreadable files; `min_cue_chars` is a
  WARN; the `ja` preset fills only unset fields; the transcript's content,
  not its path, enters the identity; delivery_package support.
- **Stinger cut point** (`test_stinger_checks.py`) - ADR-024: the
  opaque rule at every bit depth (253 passes, 252 fails; ProRes 4444's
  12-bit alpha), the stated cover window's edges, no alpha / a cut point
  between frames / past the end UNKNOWN; a delivered edit's cut frame
  against the stinger (placed right PASS, one frame late FAIL, exactly
  the limit PASS), unreadable or missing stinger files, the stinger's
  content in the identity, kind delivery and delivery_package.
- **Loop seams** (`test_loop_seam_checks.py`) - ADR-025: the click
  ratio, RMS and band-shape measures and the FFT against a direct DFT;
  a seamless loop and a bed repeated 10 times PASS, a naive cut FAIL at
  every seam with seconds, a level step and a spectral step each fail
  only their own check, every limit exact at its edge, silence PASS, a
  click out of silence and a one-channel click FAIL, windows that do not
  fit / a window under two samples / seams past the end / no samples /
  a NaN sample / audio over the sample cap / no audio stream / no ffmpeg
  UNKNOWN, the top octave band reaching the Nyquist frequency, kind
  delivery and delivery_package.
- **Title, stinger and loop material** (`test_video_material_checks.py`)
  - ADR-026/027/028: a seamless loop and a whole-period pan PASS, each
  one frame short FAIL with the seam frames, the ratio edge exact, a
  title.json loop segment PASS and one frame short FAIL, one-frame /
  undecodable / off-frame / past-the-end UNKNOWN; straight declared
  straight PASS, declared premultiplied FAIL the mode check, a
  premultiplied file composited as straight FAIL the fringe check with
  its width, no alpha / nothing opaque UNKNOWN; a key-fill export pair
  PASS, the key a frame late FAIL at frame 1, a lit pixel FAIL at frame
  10, size / frame count / timecode mismatch FAIL before any pixel, the
  key range override, the key's content in the identity, kind
  delivery_package, no ffmpeg UNKNOWN.
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
