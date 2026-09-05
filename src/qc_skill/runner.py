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
    kill the whole tree, not just the direct child.
  * ``-protocol_whitelist file`` is always set so an input path can never
    be reinterpreted as a network/pipe/concat source by ffmpeg/ffprobe.
"""

from __future__ import annotations

import os
import platform
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

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


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            os.killpg(proc.pid, 9)
    except Exception:
        pass


def run_argv(argv: List[str], timeout: Optional[float] = None) -> ProcessResult:
    for arg in argv:
        if not isinstance(arg, str):
            raise error("INTERNAL_ERROR", "non-string argv element", argv=argv)
        if "\x00" in arg:
            raise error("INTERNAL_ERROR", "argv element contains a NUL byte")

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
        stdout, stderr = proc.communicate()
        timed_out = True

    return ProcessResult(
        argv=argv,
        returncode=proc.returncode if not timed_out else -1,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        seconds=time.monotonic() - start,
        timed_out=timed_out,
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
