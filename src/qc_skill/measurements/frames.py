"""Frame readout helpers shared by the loop-seam, alpha-edge and key / fill
measurements (ADR-026/027/028).

Frames are located exactly as for the stinger cut point (ADR-024): frame
``k`` is at ``k / frame_rate`` from the stream's first frame, a range is
decoded by an input seek to a quarter frame before its first frame, and
every decoded frame's own timestamp (``showinfo``) must name the frame
expected there, else the readout is refused with the time it landed on.
"""

from __future__ import annotations

import json
import operator
import re
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..errors import error
from ..runner import ffmpeg_rawvideo_argv, run_argv
from .stinger import SEEK_LEAD

_SHOWINFO_RE = re.compile(r"Parsed_showinfo[^\n]*?\bn:\s*(\d+)\s+pts:\s*-?\d+\s+pts_time:\s*(-?[\d.]+)")


def count_frames(ffprobe_path: str, path: Path, stream_index: int, timeout: float = 120.0) -> Tuple[Optional[int], Optional[str]]:
    """``(frames, None)`` - the stream's packet count, read by demuxing
    the whole file without decoding it (one packet per frame for the
    codecs a title, stinger or key is delivered in) - or ``(None, reason)``."""

    argv = [
        ffprobe_path, "-hide_banner", "-v", "error", "-select_streams", str(int(stream_index)),
        "-count_packets", "-show_entries", "stream=nb_read_packets", "-of", "json", str(path),
    ]
    try:
        res = run_argv(argv, timeout=timeout)
    except FileNotFoundError:
        raise error("DEPENDENCY_ERROR", "ffprobe executable could not be run", executable=ffprobe_path)
    if res.timed_out:
        raise error("TOOL_ERROR", "ffprobe packet count timed out", argv=argv, timeout=timeout)
    try:
        streams = json.loads(res.stdout or "{}").get("streams") or []
        frames = int(streams[0]["nb_read_packets"])
    except (ValueError, KeyError, IndexError, TypeError):
        return None, "ffprobe could not count the stream's frames"
    return frames, None


def grab_frames(
    ffmpeg_path: str,
    path: Path,
    stream_index: int,
    first: int,
    count: int,
    rate: Fraction,
    filters: List[str],
    *,
    pix_fmt: str,
    frame_bytes: int,
    origin: float = 0.0,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Decode up to ``count`` consecutive frames starting at frame
    ``first`` through ``filters`` as ``pix_fmt`` and return
    ``{frames: [{index, time, raw}]}`` (``time`` is the verified frame's
    ``index / rate``) - fewer than ``count`` when the
    stream ends first - or ``{reason}`` when no frame was decoded or a
    decoded frame is not the one expected at its place."""

    seek = origin + float(max(Fraction(0), (first - SEEK_LEAD) / rate))
    argv = ffmpeg_rawvideo_argv(
        ffmpeg_path, path, filters + ["showinfo"], stream_select=f"0:{stream_index}",
        pix_fmt=pix_fmt, seek_sec=seek, max_frames=count, log_level="info",
    )
    try:
        res = run_argv(argv, timeout=timeout, binary_stdout=True)
    except FileNotFoundError:
        raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)
    if res.timed_out:
        raise error("TOOL_ERROR", "ffmpeg frame readout timed out", argv=argv, timeout=timeout)
    infos = _SHOWINFO_RE.findall(res.stderr or "")
    data = res.stdout_bytes or b""
    n = min(len(infos), len(data) // frame_bytes) if frame_bytes > 0 else 0
    when = f"frame {first} ({float(first / rate):.6f}s)"
    if n == 0:
        return {"reason": f"no decodable frame at {when}"}
    frames: List[Dict[str, Any]] = []
    for i in range(n):
        time_sec = float(f"{float(seek - origin) + float(infos[i][1]):.6f}")
        got = int(round(Fraction(time_sec).limit_denominator(10 ** 9) * rate))
        if got != first + i:
            return {
                "reason": f"the decoder returned the frame at {time_sec}s (frame {got}) where frame {first + i} was expected",
            }
        frames.append({"index": got, "time": round(float(got / rate), 6), "raw": data[i * frame_bytes:(i + 1) * frame_bytes]})
    return {"frames": frames}


def mean_abs_difference(a: bytes, b: bytes) -> float:
    """Mean absolute difference of two equal-length byte strings."""

    if a == b:
        return 0.0
    return sum(map(abs, map(operator.sub, a, b))) / len(a)
