# Security

## Absolute rules (STEP 15 of the task spec)

- `subprocess.Popen` is always called with an argv **list**. `shell=True`
  is never used anywhere in this package (`src/qc_skill/runner.py`).
- The request schema (`src/qc_skill/schemas.py`) rejects `command`,
  `commands`, `argv`, `args`, `shell`, `cmd`, `cmdline`, `exec`,
  `executable`, `filter`, `filter_complex`, `env`, `environment` outright,
  at **any nesting depth** in the request document (`_find_forbidden`
  walks the full structure, including inside `parameters` and `rules`).
  There is no way for a request to influence what executable runs or what
  filter graph is built.
- Every ffmpeg/ffprobe invocation is built from a **fixed** set of flags
  plus exactly one resolved, validated path, in
  `src/qc_skill/runner.py::ffprobe_argv` /
  `ffmpeg_analysis_argv`. Detection filters (`blackdetect`,
  `freezedetect`, `ebur128`, `astats`, `silencedetect`) are always
  constructed internally from numeric parameters (thresholds,
  min-durations) - a caller can tune *sensitivity*, never inject arbitrary
  filter syntax.
- `-protocol_whitelist file` is always set, so an input path can never be
  reinterpreted by ffmpeg as a network/pipe/concat/device source.
- The child process runs with a minimal, explicit environment (`PATH`,
  `HOME`, locale/tmp vars, and the Windows system vars needed to launch a
  process at all) - secrets in the parent environment are never inherited.
- No `eval()`, `exec()`, or dynamic execution of any caller-supplied text,
  anywhere. Rule evaluation (`src/qc_skill/rules.py`) is a fixed set of
  Python comparisons against typed dataclass fields.

## PathPolicy (STEP 16)

`src/qc_skill/security.py::PathPolicy`:

- **Input** (`resolve_input`): rejects empty strings, NUL bytes, control
  characters, and any `..` path segment in the *raw* string before any
  filesystem resolution happens. The path is then resolved with symlinks
  followed (`Path.resolve(strict=True)`), must be an existing regular
  file, and - when `allowed_input_roots` is configured - must be a
  descendant of one of those roots using component-wise comparison
  (`Path.relative_to`), never a string prefix check (so `/w/media` never
  matches `/w/media_evil`). A symlink inside an allowed root that points
  outside it is rejected, because containment is checked *after*
  resolution.
- **Output/report** (`resolve_output`): rejects absolute paths and `..`
  segments outright, validates the filename (`check_filename`: rejects
  Windows-reserved device names `CON`/`PRN`/`AUX`/`NUL`/`COM1-9`/`LPT1-9`,
  control characters, a trailing space or dot, an argument that looks like
  an option flag, and Windows-reserved characters `<>:"|?*`), then
  resolves the *parent* directory (following any symlinks that already
  exist) before re-appending the leaf name - so a symlinked directory
  cannot be used to smuggle the final path outside the workspace.
- **Default posture**: when `allowed_input_roots` is not configured, any
  readable regular file is accepted (matching the rest of the skill
  ecosystem's default - see `media-analysis-skill/docs/security.md`).
  Restricting inputs to specific roots is the caller's choice, made via
  `qc run --allowed-input-root DIR` (repeatable), never via the request
  body.
- **Current usage note**: today's `run` operation only ever writes the
  report to stdout - there is no "write a report to this caller-named
  path" operation yet, so `resolve_output` is not on the current `run`
  call path. It is exercised directly by `tests/test_security.py` and is
  the boundary any future file-output flag (or the report cache, if its
  layout ever stops being a fixed, non-request-derived hash filename)
  must go through. The report cache itself (`--cache-dir`) is a plain,
  unvalidated CLI flag today, at the same trust tier as `--workspace` -
  its filenames are always a sha256 hex digest computed internally, never
  derived from request content, so this is not a path-injection surface.

`--workspace` and `--allowed-input-root` are always CLI flags supplied by
the process invoking `qc run` - never fields inside the JSON request. This
means a request can never grant itself a wider filesystem view than its
caller already intended.

## What is *not* enforced

- Resource limits (memory, disk, CPU) beyond a wall-clock `timeout` per
  ffmpeg/ffprobe invocation.
- Confidentiality of the report cache directory - paths and fingerprints
  inside cached reports are plaintext (though tamper-detected via a stored
  content hash; see `docs/architecture.md#reuse--cache`).
- Validation of *media content* safety (e.g. decoder CVEs) - `qc-skill`
  relies on the ffmpeg/ffprobe build on `PATH` being reasonably current;
  `doctor --json` reports the exact version in use so a caller can decide
  whether it is acceptable.

## Tested attack surface (`tests/test_security_injection.py`, `tests/test_security.py`)

- Forbidden request keys (`command`, `argv`, `shell`, `executable`,
  `filter`, `filter_complex`, `env`), including nested inside `rules`.
- Shell metacharacters in `input` are treated as a literal, non-existent
  filename (never interpreted by a shell) - the request fails with
  `MISSING_INPUT`, not code execution.
- Path traversal (`../`, embedded `..\\`), NUL-byte injection, prefix
  collision (`/w/media` vs. `/w/media_evil`), and symlink escape for both
  input and output paths.
- Windows-reserved filenames and unsafe filename characters, tested
  independent of the host OS.
