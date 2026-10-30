"""Fixed, validated adapters for invoking ffprobe/ffmpeg.

Security boundary (see docs/security.md and STEP 15 of the task spec):
  * subprocess.Popen is always called with an argv *list* - shell=True is
    never used anywhere in this package.
  * The request/parameters model (schemas.py) never accepts a raw
    command, argv, filter string, or executable path from the caller.
    Every invocation below is built entirely from fixed flag lists plus a
    single resolved, validated input path.
  * The child runs in a minimal, explicit environment (no inherited
    secrets) and as the leader of its own process group so a timeout can
    kill the whole tree, not just the direct child (POSIX: a new session,
    ``os.killpg``; Windows: a new process group, ``taskkill /T /F``).
  * ``request.timeout`` is one wall-clock budget for the whole request
    (ADR-019): ``request_deadline`` sets it, every child gets at most the
    time that is left, and running out raises ``TIMEOUT`` after the
    child's process group has been killed.
  * ``-protocol_whitelist file`` is always set so an input path can never
    be reinterpreted as a network/pipe/concat source by ffmpeg/ffprobe.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, List, Optional

from .errors import error

_PASSTHROUGH_ENV_VARS = (
    "PATH", "HOME", "LANG", "LC_ALL", "TERM",
    "TMPDIR", "TEMP", "TMP",
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
)


@dataclass
class ProcessResult:
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool = False
    stdout_bytes: bytes = b""


def _clean_env() -> dict:
    env = {}
    for name in _PASSTHROUGH_ENV_VARS:
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def _group_kwargs() -> dict:
    if platform.system() == "Windows":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _taskkill() -> str:
    system_root = os.environ.get("SYSTEMROOT")
    if system_root:
        candidate = os.path.join(system_root, "System32", "taskkill.exe")
        if os.path.isfile(candidate):
            return candidate
    return "taskkill"


def _kill_group(proc: subprocess.Popen) -> None:
    """Kill the child and everything it started. Never raises: the child
    may already have exited by the time this runs."""

    if platform.system() == "Windows":
        try:
            subprocess.run(
                [_taskkill(), "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            pass
    try:
        proc.kill()  # the direct child, in case the group kill did not reach it
    except Exception:
        pass


# --- request.timeout (ADR-019) ---------------------------------------------

# (deadline on time.monotonic(), budget in seconds) for the request being
# run in this context, or None when the request set no timeout.
_DEADLINE: "contextvars.ContextVar[Optional[tuple]]" = contextvars.ContextVar("qc_request_deadline", default=None)


@contextlib.contextmanager
def request_deadline(seconds: Optional[float]) -> Iterator[None]:
    """Run the enclosed block under one wall-clock budget of ``seconds``
    (``None``: no budget). Every ``run_argv`` call inside it is bounded by
    the time left; see ``check_deadline`` for the checks between children.
    Nested budgets never extend an enclosing one: the earlier deadline wins
    (the CLI opens one around capability detection, ``run_report`` opens
    another with the same budget)."""

    outer = _DEADLINE.get()
    inner = None if seconds is None else (time.monotonic() + float(seconds), float(seconds))
    if outer is not None and (inner is None or outer[0] <= inner[0]):
        inner = outer
    token = _DEADLINE.set(inner)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def _timeout_error(budget: float, stage: str, **details):
    return error(
        "TIMEOUT",
        f"request.timeout of {budget:g}s elapsed during {stage}",
        timeout=budget, stage=stage, **details,
    )


def check_deadline(stage: str) -> None:
    """Raise ``TIMEOUT`` if the current request's budget is spent."""

    current = _DEADLINE.get()
    if current is not None and time.monotonic() >= current[0]:
        raise _timeout_error(current[1], stage)


def run_argv(argv: List[str], timeout: Optional[float] = None, *, binary_stdout: bool = False) -> ProcessResult:
    """``binary_stdout=True`` keeps stdout as raw bytes (``stdout_bytes``;
    ``stdout`` is then empty) - for the rawvideo frame readouts in
    measurements/temporal.py and measurements/text_legibility.py, which
    must never be decoded as text.

    ``timeout`` is this call's own ceiling; when it is the one that runs
    out the result comes back with ``timed_out=True`` and the caller turns
    it into ``TOOL_ERROR``. When the request's ``request_deadline`` runs out
    first, the child's process group is killed and ``TIMEOUT`` is raised
    here instead.
    """

    for arg in argv:
        if not isinstance(arg, str):
            raise error("INTERNAL_ERROR", "non-string argv element", argv=argv)
        if "\x00" in arg:
            raise error("INTERNAL_ERROR", "argv element contains a NUL byte")

    stage = os.path.basename(argv[0]) if argv else "subprocess"
    current = _DEADLINE.get()
    deadline_binds = False
    if current is not None:
        remaining = current[0] - time.monotonic()
        if remaining <= 0:
            raise _timeout_error(current[1], stage)
        if timeout is None or remaining <= timeout:
            timeout = remaining
            deadline_binds = True

    start = time.monotonic()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_clean_env(),
        shell=False,
        **_group_kwargs(),
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:  # a pipe still held open by something the kill missed
            proc.kill()
            proc.wait()
            stdout, stderr = b"", b""
        timed_out = True
        if deadline_binds:
            raise _timeout_error(current[1], stage, seconds=round(time.monotonic() - start, 3))

    return ProcessResult(
        argv=argv,
        returncode=proc.returncode if not timed_out else -1,
        stdout="" if binary_stdout else stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        seconds=time.monotonic() - start,
        timed_out=timed_out,
        stdout_bytes=stdout if binary_stdout else b"",
    )


def ffprobe_argv(executable: str, input_path: Path, extra: Optional[List[str]] = None) -> List[str]:
    argv = [
        executable,
        "-hide_banner",
        "-v", "error",
        "-protocol_whitelist", "file",
        "-print_format", "json",
    ]
    if extra:
        argv.extend(extra)
    argv.extend(["-i", str(input_path)])
    return argv


def ffmpeg_analysis_argv(
    executable: str,
    input_path: Path,
    filters: List[str],
    *,
    audio: bool,
    stream_select: Optional[str] = None,
) -> List[str]:
    """Build a fixed 'decode and measure, write nothing' ffmpeg invocation.

    ``filters`` is a list of already-escaped, internally constructed filter
    expressions - never caller-supplied text (see security.py / schemas.py:
    the request model rejects any 'filter'/'filter_complex' key outright).
    """

    argv = [
        executable,
        "-hide_banner",
        "-nostdin",
        "-v", "info",
        "-protocol_whitelist", "file",
        "-i", str(input_path),
    ]
    if stream_select:
        argv.extend(["-map", stream_select])
    if filters:
        flag = "-af" if audio else "-vf"
        argv.extend([flag, ",".join(filters)])
    argv.extend(["-f", "null", "-"])
    return argv


def ffmpeg_rawvideo_argv(
    executable: str,
    input_path: Path,
    filters: List[str],
    *,
    stream_select: str,
    pix_fmt: Optional[str],
    seek_sec: Optional[float] = None,
    max_frames: Optional[int] = None,
    log_level: str = "info",
) -> List[str]:
    """Build a fixed 'decode, filter, write raw frames to stdout' ffmpeg
    invocation (``pix_fmt=None`` writes nothing: ``-f null``).

    Like ``ffmpeg_analysis_argv``, ``filters`` are internally constructed
    expressions whose only caller-derived parts are numbers that
    schemas.py has already validated as numbers - never caller text.
    """

    argv = [executable, "-hide_banner", "-nostdin", "-v", log_level, "-protocol_whitelist", "file"]
    if seek_sec is not None:
        argv.extend(["-ss", f"{seek_sec:.6f}"])
    argv.extend(["-i", str(input_path), "-map", stream_select])
    if max_frames is not None:
        argv.extend(["-frames:v", str(int(max_frames))])
    if filters:
        argv.extend(["-vf", ",".join(filters)])
    if pix_fmt is None:
        argv.extend(["-f", "null", "-"])
    else:
        argv.extend(["-f", "rawvideo", "-pix_fmt", pix_fmt, "-"])
    return argv
