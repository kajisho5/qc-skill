"""ffprobe wrapper: raw container/stream metadata, structured (not raw stdout).

STEP 3 of the task spec is explicit that "simply returning ffprobe/FFmpeg
stdout as-is is not a QC skill" - this module is the one place that talks
to ffprobe, and everything downstream (measurements/video.py,
measurements/audio.py) consumes its parsed, typed return value, never raw
JSON text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .errors import error
from .runner import ffprobe_argv, run_argv


def probe_media(ffprobe_path: str, input_path: Path, timeout: float = 60.0) -> Dict[str, Any]:
    """Run ffprobe -show_format -show_streams -show_error and parse it.

    Raises UNSUPPORTED_FORMAT if ffprobe cannot open/parse the container at
    all (this is a fact about the input, not a tool failure) and
    DEPENDENCY_ERROR if ffprobe itself could not be executed.
    """

    argv = ffprobe_argv(
        ffprobe_path,
        input_path,
        extra=["-show_format", "-show_streams", "-show_error"],
    )
    try:
        result = run_argv(argv, timeout=timeout)
    except FileNotFoundError:
        raise error("DEPENDENCY_ERROR", "ffprobe executable could not be run", executable=ffprobe_path)

    if result.timed_out:
        raise error("TOOL_ERROR", "ffprobe timed out", argv=argv, timeout=timeout)

    try:
        data = json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError:
        raise error(
            "UNSUPPORTED_FORMAT",
            "ffprobe produced no parseable output for this input",
            stderr=result.stderr[-2000:],
        )

    if "error" in data:
        raise error(
            "UNSUPPORTED_FORMAT",
            f"ffprobe could not open the input: {data['error'].get('string', 'unknown error')}",
            ffprobe_error=data["error"],
        )

    if result.returncode != 0 and not data.get("format") and not data.get("streams"):
        raise error(
            "UNSUPPORTED_FORMAT",
            "ffprobe exited with an error and produced no usable metadata",
            stderr=result.stderr[-2000:],
        )

    data.setdefault("format", {})
    data.setdefault("streams", [])
    return data


def video_streams(probe_data: Dict[str, Any]) -> list:
    return [s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"]


def audio_streams(probe_data: Dict[str, Any]) -> list:
    return [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]


def parse_frame_rate(rate: Optional[str]) -> Optional[float]:
    if not rate or rate in ("0/0", "N/A"):
        return None
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            num_f, den_f = float(num), float(den)
        except ValueError:
            return None
        if den_f == 0:
            return None
        return num_f / den_f
    try:
        return float(rate)
    except ValueError:
        return None
