"""Video loop-seam measurement (ADR-026) - measured facts only.

A loop is seen end -> start: after its last frame the player shows its
first again. ``video.loop_seam`` measures that step against the motion
inside the loop next to it:

  * the loop is the whole file, or - with ``segments`` from the
    material's title.json (``{in, loop, out}`` seconds) - the frames
    ``in <= t < in + loop``; both ends must be on a frame (within 1/1000
    of one);
  * the ``window_frames + 1`` first and last frames of the loop are
    decoded as 8-bit RGB at the stream's own size;
  * ``seam.difference`` is the mean absolute difference (0-255) of the
    last frame and the first; ``inside`` lists the same for each pair of
    consecutive frames in the two windows, and ``reference`` is their
    median, so one cut or flash next to the seam does not set it;
  * ``ratio`` is ``seam.difference / reference``: about 1 for a loop
    that moves on across its seam as it moves inside, 2 for a pan cut one
    frame short. On a still picture (``reference`` 0) the ratio is 0 when
    the seam is still too and ``null`` (``still: true``) when it is not.

Pure Python + ffmpeg/ffprobe, no numpy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import QCMeasurement
from .frames import count_frames, grab_frames, mean_abs_difference
from .stinger import ON_FRAME_TOLERANCE, _stream_origin, frame_at, stream_frame_rate

DEFAULT_WINDOW_FRAMES = 4
MAX_WINDOW_FRAMES = 12
MIN_LOOP_FRAMES = 3

NOTE = (
    "mean absolute difference, 8-bit RGB (0-255), of the loop's last and first frames (seam) and of each pair of "
    "consecutive frames in the window_frames next to it on both sides (inside); ratio = seam / median(inside)"
)


def _median(values: List[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _measure(
    ffmpeg_path: str, ffprobe_path: str, path: Path, probe_data: Dict[str, Any], stream: Dict[str, Any], rule: Any,
) -> Dict[str, Any]:
    window = rule.window_frames if rule.window_frames is not None else DEFAULT_WINDOW_FRAMES
    entry: Dict[str, Any] = {"measured": False, "window_frames": window, "source": "segments" if rule.segments else "file"}
    rate = stream_frame_rate(stream)
    if rate is None:
        entry["reason"] = "the stream's frame rate could not be measured"
        return entry
    entry["frame_rate"] = f"{rate.numerator}/{rate.denominator}"
    width, height = stream.get("width"), stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        entry["reason"] = "the stream's frame size could not be measured"
        return entry
    stream_index = int(stream["index"])
    if rule.segments is not None:
        start, end = rule.segments["in"], rule.segments["in"] + rule.segments["loop"]
        first, off_first = frame_at(start, rate)
        end_frame, off_end = frame_at(end, rate)
        for name, t, n, off in (("in", start, first, off_first), ("in + loop", end, end_frame, off_end)):
            if off > ON_FRAME_TOLERANCE:
                entry["reason"] = (
                    f"segments {name} = {t}s is not on a frame: the nearest is frame {n} ({float(n / rate):.6f}s), "
                    f"{off:.3f} frame away"
                )
                return entry
        last = end_frame - 1
        file_frames = None
    else:
        file_frames, reason = count_frames(ffprobe_path, path, stream_index)
        if file_frames is None:
            entry["reason"] = reason
            return entry
        first, last = 0, file_frames - 1
    frames = last - first + 1
    entry["loop"] = {"first_frame": first, "last_frame": last, "frames": frames}
    if frames < MIN_LOOP_FRAMES:
        entry["reason"] = f"the loop has {max(frames, 0)} frame(s); its seam is compared with the motion inside, which needs at least {MIN_LOOP_FRAMES}"
        return entry
    k = min(window, frames - 1)
    frame_bytes = width * height * 3
    origin = _stream_origin(probe_data, stream)

    def read(start_frame: int, n: int) -> Dict[str, Any]:
        return grab_frames(
            ffmpeg_path, path, stream_index, start_frame, n, rate, ["format=rgb24"],
            pix_fmt="rgb24", frame_bytes=frame_bytes, origin=origin,
        )

    head = read(first, k + 1)
    if "reason" in head:
        entry["reason"] = head["reason"]
        return entry
    # For the whole file one frame more is asked for: if it comes, the file
    # holds more frames than ffprobe counted and the last one is not known.
    tail = read(last - k, k + 1 if file_frames is None else k + 2)
    if "reason" in tail:
        entry["reason"] = tail["reason"]
        return entry
    head_f, tail_f = head["frames"], tail["frames"]
    if len(head_f) < k + 1:
        entry["reason"] = f"the stream ended at frame {head_f[-1]['index']}, inside the loop"
        return entry
    if len(tail_f) < k + 1:
        entry["reason"] = (
            f"the stream ended at frame {tail_f[-1]['index']}, before the loop's last frame {last} ({float(last / rate):.6f}s)"
        )
        return entry
    if len(tail_f) > k + 1:
        entry["reason"] = f"the file has frames after frame {last}, the last one ffprobe counted"
        return entry

    pairs: Dict[tuple, float] = {}
    for run in (head_f, tail_f):
        for a, b in zip(run, run[1:]):
            pairs[(a["index"], b["index"])] = round(mean_abs_difference(a["raw"], b["raw"]), 3)
    inside = [{"from_frame": a, "to_frame": b, "difference": d} for (a, b), d in sorted(pairs.items())]
    seam_diff = round(mean_abs_difference(tail_f[-1]["raw"], head_f[0]["raw"]), 3)
    reference = round(_median([p["difference"] for p in inside]), 3)
    if reference > 0:
        ratio: Optional[float] = round(seam_diff / reference, 3)
        still = False
    else:
        ratio, still = (0.0, False) if seam_diff == 0 else (None, True)
    entry.update(
        measured=True,
        seam={
            "from_frame": last, "to_frame": first, "from_time": tail_f[-1]["time"], "to_time": head_f[0]["time"],
            "difference": seam_diff,
        },
        inside=inside, reference=reference, ratio=ratio, still=still, width=width, height=height,
    )
    return entry


def analyze_video_loop_seam(
    ffmpeg_path: str, ffprobe_path: str, input_path: Path, probe_data: Dict[str, Any], stream: Dict[str, Any], rule: Any,
) -> List[QCMeasurement]:
    value = _measure(ffmpeg_path, ffprobe_path, input_path, probe_data, stream, rule)
    return [QCMeasurement("video.loop_seam", "video", "loop_seam", value, source="ffmpeg:rawvideo", notes=NOTE)]


def not_performed(rule: Any, reason: str) -> List[QCMeasurement]:
    window = rule.window_frames if rule.window_frames is not None else DEFAULT_WINDOW_FRAMES
    value = {"measured": False, "window_frames": window, "source": "segments" if rule.segments else "file", "reason": reason}
    return [QCMeasurement("video.loop_seam", "video", "loop_seam", value, source="ffmpeg:rawvideo", notes=NOTE)]
