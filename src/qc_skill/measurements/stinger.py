"""Stinger cut-point measurements (ADR-024) - measured facts only.

A stinger is a graphic with an alpha channel laid over a join; the edit
switches clips at the stinger's cut point, the frame where the graphic
covers the whole picture. Two facts are measured, each only when a
``VideoRule.stinger`` asks for it:

  * ``video.stinger_cut_point_alpha`` - the alpha of every pixel of the
    stinger's frame at its cut point. The frame is the one at
    ``cut_point * frame_rate`` (which must be a whole frame, within
    1/1000 of one); it is decoded by an input seek to a quarter frame
    before it, and the decoded frame's own timestamp is checked to be
    that frame. ``extractplanes=a`` writes the alpha plane at its native
    bit depth (8, 10, 12 or 16 bit). A pixel covers when its alpha is at
    least 253 of 255 on that scale (``alpha * 255 >= 253 * max``), the
    rule video-editing-skill applies before it joins (2 of 255 of
    tolerance). A stream whose pixel format has no alpha channel
    (ffprobe's own pixel-format flags) is not measured: an alpha that was
    not read is never taken as opaque.
  * ``video.stinger_cut_frames`` - for each cut time in a delivered
    video, the mean absolute difference (8-bit RGB, 0-255) between the
    delivered frame there and the stinger's frame at its cut point
    (scaled to the delivered size), plus the same for the frames just
    before and after, as context. At a cut under full cover the
    delivered frame *is* the stinger's frame, so the difference is only
    the delivery's encoding loss.

Times are seconds from the first frame of the video stream; frame ``k``
is at ``k / frame_rate``. Pure Python + ffmpeg/ffprobe, no numpy.
"""

from __future__ import annotations

import json
import operator
import re
import sys
from array import array
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..errors import QCError, error
from ..models import QCMeasurement
from ..probe import probe_media, video_streams
from ..runner import ffmpeg_rawvideo_argv, run_argv

OPAQUE_MIN_ALPHA = 253  # of ALPHA_SCALE: a pixel covers when its alpha is at least this (2 of 255 of tolerance)
ALPHA_SCALE = 255
ON_FRAME_TOLERANCE = Fraction(1, 1000)  # of a frame: how far a stated time may sit from the frame it names
SEEK_LEAD = Fraction(1, 4)  # of a frame: the seek lands a quarter frame before the frame it wants

ALPHA_NOTE = (
    f"alpha plane of the stinger's frame at its cut point (extractplanes=a, native bit depth); a pixel covers when "
    f"alpha * {ALPHA_SCALE} >= {OPAQUE_MIN_ALPHA} * max (at least {OPAQUE_MIN_ALPHA} of {ALPHA_SCALE})"
)
CUT_FRAMES_NOTE = (
    "mean absolute difference, 8-bit RGB (0-255), between the delivered frame at each cut time and the stinger's "
    "frame at its cut point scaled to the delivered size; difference_before/after are the neighbouring frames"
)

_SHOWINFO_FIRST_RE = re.compile(r"Parsed_showinfo[^\n]*?\bn:\s*0\s+pts:\s*-?\d+\s+pts_time:\s*(-?[\d.]+)[^\n]*")
_FMT_RE = re.compile(r"\bfmt:(\S+)")
_SIZE_RE = re.compile(r"\bs:(\d+)x(\d+)")
_GRAY_FMT_RE = re.compile(r"^gray(\d+)?(le|be)?$")

_ALPHA_FORMATS: Dict[str, Optional[Set[str]]] = {}


def alpha_pixel_formats(ffprobe_path: str) -> Optional[Set[str]]:
    """Names of the pixel formats ffprobe flags as carrying alpha, or
    ``None`` when the list could not be read. Cached per executable."""

    if ffprobe_path in _ALPHA_FORMATS:
        return _ALPHA_FORMATS[ffprobe_path]
    names: Optional[Set[str]] = None
    try:
        result = run_argv(
            [ffprobe_path, "-hide_banner", "-v", "error", "-show_pixel_formats",
             "-show_entries", "pixel_format=name:flags=alpha", "-of", "json"],
            timeout=15,
        )
        if result.returncode == 0 and not result.timed_out:
            doc = json.loads(result.stdout or "{}")
            names = {
                p["name"] for p in doc.get("pixel_formats", [])
                if isinstance(p, dict) and isinstance(p.get("name"), str) and (p.get("flags") or {}).get("alpha") == 1
            } or None
    except (FileNotFoundError, ValueError):
        names = None
    _ALPHA_FORMATS[ffprobe_path] = names
    return names


def stream_frame_rate(stream: Dict[str, Any]) -> Optional[Fraction]:
    """The stream's exact frame rate (``r_frame_rate``, else
    ``avg_frame_rate``) as a fraction; ``None`` when neither is usable."""

    for key in ("r_frame_rate", "avg_frame_rate"):
        raw = stream.get(key)
        if not isinstance(raw, str) or "/" not in raw:
            continue
        num, _, den = raw.partition("/")
        try:
            rate = Fraction(int(num), int(den))
        except (ValueError, ZeroDivisionError):
            continue
        if rate > 0:
            return rate
    return None


def frame_at(seconds: float, rate: Fraction) -> Tuple[int, float]:
    """The frame index a time names and how far (in frames) the time sits
    from that frame."""

    exact = Fraction(seconds).limit_denominator(10 ** 9) * rate
    n = int(round(exact))
    return n, float(abs(exact - n))


def _stream_origin(probe_data: Dict[str, Any], stream: Dict[str, Any]) -> float:
    """Where the stream's first frame sits on the file's timeline (an
    input seek counts from the file's start)."""

    try:
        return max(0.0, float(stream.get("start_time")) - float(probe_data.get("format", {}).get("start_time")))
    except (TypeError, ValueError):
        return 0.0


def _gray_depth(fmt: str) -> Optional[Tuple[int, str]]:
    m = _GRAY_FMT_RE.match(fmt)
    if not m:
        return None
    if m.group(1) is None:
        return (8, "little") if m.group(2) is None else None
    depth = int(m.group(1))
    if not 9 <= depth <= 16 or m.group(2) is None:
        return None
    return depth, "little" if m.group(2) == "le" else "big"


def grab_frame(
    ffmpeg_path: str,
    path: Path,
    stream_index: int,
    frame: int,
    rate: Fraction,
    filters: List[str],
    *,
    pix_fmt: Optional[str],
    origin: float = 0.0,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    """Decode exactly frame ``frame`` (``frame / rate`` seconds from the
    stream's first frame) through ``filters`` and return
    ``{raw, fmt, width, height, time}``, or ``{reason}`` when that frame
    could not be decoded. ``pix_fmt=None`` keeps the filters' own output
    format."""

    seek = origin + float(max(Fraction(0), (frame - SEEK_LEAD) / rate))
    argv = ffmpeg_rawvideo_argv(
        ffmpeg_path, path, filters + ["showinfo"], stream_select=f"0:{stream_index}",
        pix_fmt=pix_fmt, keep_pix_fmt=pix_fmt is None, seek_sec=seek, max_frames=1, log_level="info",
    )
    try:
        res = run_argv(argv, timeout=timeout, binary_stdout=True)
    except FileNotFoundError:
        raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)
    if res.timed_out:
        raise error("TOOL_ERROR", "ffmpeg stinger frame grab timed out", argv=argv, timeout=timeout)
    when = f"frame {frame} ({float(frame / rate):.6f}s)"
    info = _SHOWINFO_FIRST_RE.search(res.stderr)
    if info is None or not res.stdout_bytes:
        return {"reason": f"no decodable frame at {when}"}
    line = info.group(0)
    fmt_m, size_m = _FMT_RE.search(line), _SIZE_RE.search(line)
    if fmt_m is None or size_m is None:
        return {"reason": f"the decoded frame at {when} did not report its format and size"}
    time_sec = float(f"{float(seek - origin) + float(info.group(1)):.6f}")
    got = int(round(Fraction(time_sec).limit_denominator(10 ** 9) * rate))
    if got != frame:
        return {"reason": f"the decoder returned the frame at {time_sec}s (frame {got}), not {when}"}
    return {
        "raw": res.stdout_bytes, "fmt": fmt_m.group(1),
        "width": int(size_m.group(1)), "height": int(size_m.group(2)), "time": time_sec,
    }


def _media_stream(ffprobe_path: str, path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
    try:
        probe_data = probe_media(ffprobe_path, path)
    except QCError as exc:
        if exc.code != "UNSUPPORTED_FORMAT":
            raise
        return None, None, f"the stinger file could not be read: {exc.message}"
    streams = video_streams(probe_data)
    if not streams:
        return None, None, "the stinger file has no video stream"
    return probe_data, streams[0], None


def _alpha_entry(
    ffmpeg_path: str, ffprobe_path: str, path: Path, probe_data: Dict[str, Any], stream: Dict[str, Any],
    cut_point: float, source: str,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"source": source, "cut_point": cut_point, "measured": False}
    pix_fmt = stream.get("pix_fmt")
    entry["pix_fmt"] = pix_fmt
    formats = alpha_pixel_formats(ffprobe_path)
    if formats is None:
        entry["reason"] = "ffprobe could not list which pixel formats carry alpha"
        return entry
    if not pix_fmt or pix_fmt not in formats:
        entry["reason"] = f"the stream's pixel format {pix_fmt!r} has no alpha channel"
        return entry
    rate = stream_frame_rate(stream)
    if rate is None:
        entry["reason"] = "the stream's frame rate could not be measured"
        return entry
    entry["frame_rate"] = f"{rate.numerator}/{rate.denominator}"
    frame, off = frame_at(cut_point, rate)
    entry["frame"] = frame
    if off > ON_FRAME_TOLERANCE:
        entry["reason"] = (
            f"the cut point {cut_point}s is not on a frame: the nearest is frame {frame} "
            f"({float(frame / rate):.6f}s), {off:.3f} frame away"
        )
        return entry
    got = grab_frame(
        ffmpeg_path, path, int(stream["index"]), frame, rate, ["extractplanes=a"],
        pix_fmt=None, origin=_stream_origin(probe_data, stream),
    )
    if "reason" in got:
        entry["reason"] = got["reason"]
        return entry
    depth = _gray_depth(got["fmt"])
    width, height = got["width"], got["height"]
    pixels = width * height
    if depth is None:
        entry["reason"] = f"the alpha plane came out as {got['fmt']!r}, which is not read"
        return entry
    bits, order = depth
    raw = got["raw"]
    if bits == 8:
        if len(raw) < pixels:
            entry["reason"] = f"the alpha plane of frame {frame} is shorter than {width}x{height}"
            return entry
        values: Any = raw[:pixels]
    else:
        if len(raw) < 2 * pixels:
            entry["reason"] = f"the alpha plane of frame {frame} is shorter than {width}x{height}"
            return entry
        values = array("H")
        values.frombytes(raw[: 2 * pixels])
        if order != sys.byteorder:
            values.byteswap()
    below, low, native_threshold = alpha_coverage(values, bits)
    top = (1 << bits) - 1
    entry.update(
        measured=True, frame_time=got["time"], width=width, height=height, alpha_format=got["fmt"], alpha_bit_depth=bits,
        pixels=pixels, pixels_below=below, below_fraction=round(below / pixels, 6),
        min_alpha=low, min_alpha_255=round(low * ALPHA_SCALE / top, 3),
        threshold={"min_alpha_255": OPAQUE_MIN_ALPHA, "scale": ALPHA_SCALE, "native": native_threshold},
    )
    return entry


def opaque_threshold(bits: int) -> int:
    """The lowest native alpha value that covers at ``bits`` bits:
    the least ``a`` with ``a * 255 >= 253 * (2**bits - 1)``."""

    top = (1 << bits) - 1
    return -(-OPAQUE_MIN_ALPHA * top // ALPHA_SCALE)


def alpha_coverage(values: Any, bits: int) -> Tuple[int, int, int]:
    """``(pixels below the threshold, lowest alpha, threshold)`` for alpha
    samples at ``bits`` bits. Pure function, exposed for unit tests."""

    threshold = opaque_threshold(bits)
    return sum(1 for v in values if v < threshold), min(values), threshold


def _mean_abs_difference(a: bytes, b: bytes) -> float:
    return sum(map(abs, map(operator.sub, a, b))) / len(a)


def _cut_frames(
    ffmpeg_path: str, input_path: Path, input_probe: Dict[str, Any], input_stream: Dict[str, Any],
    stinger_path: Path, stinger_probe: Dict[str, Any], stinger_stream: Dict[str, Any],
    cut_point: float, cut_at: List[float],
) -> Dict[str, Any]:
    def unmeasured_all(reason: str) -> Dict[str, Any]:
        return {
            "stinger_frame": {"measured": False, "reason": reason},
            "cuts": [{"cut_at": t, "measured": False, "reason": reason} for t in cut_at],
        }

    out_rate = stream_frame_rate(input_stream)
    width, height = input_stream.get("width"), input_stream.get("height")
    if out_rate is None:
        return unmeasured_all("the delivered video's frame rate could not be measured")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return unmeasured_all("the delivered video's frame size could not be measured")
    st_rate = stream_frame_rate(stinger_stream)
    if st_rate is None:
        return unmeasured_all("the stinger's frame rate could not be measured")
    st_frame, off = frame_at(cut_point, st_rate)
    if off > ON_FRAME_TOLERANCE:
        return unmeasured_all(f"the cut point {cut_point}s is not on a frame of the stinger")
    ref = grab_frame(
        ffmpeg_path, stinger_path, int(stinger_stream["index"]), st_frame, st_rate,
        ["format=rgb24", f"scale={width}:{height}:flags=bicubic"], pix_fmt="rgb24",
        origin=_stream_origin(stinger_probe, stinger_stream),
    )
    size = width * height * 3
    if "reason" in ref or len(ref.get("raw", b"")) < size:
        return unmeasured_all(f"the stinger's frame at its cut point could not be read: {ref.get('reason', 'short frame')}")
    reference = ref["raw"][:size]
    stinger_frame = {
        "measured": True, "frame": st_frame, "frame_time": ref["time"],
        "scaled_to": [width, height], "source_size": [stinger_stream.get("width"), stinger_stream.get("height")],
    }
    origin = _stream_origin(input_probe, input_stream)

    def difference(frame: int) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        got = grab_frame(
            ffmpeg_path, input_path, int(input_stream["index"]), frame, out_rate, ["format=rgb24"],
            pix_fmt="rgb24", origin=origin,
        )
        if "reason" in got:
            return None, None, got["reason"]
        if len(got["raw"]) < size:
            return None, None, f"frame {frame} of the delivered video is shorter than {width}x{height}"
        return round(_mean_abs_difference(got["raw"][:size], reference), 3), got["time"], None

    cuts: List[Dict[str, Any]] = []
    for t in cut_at:
        frame, off = frame_at(t, out_rate)
        entry: Dict[str, Any] = {"cut_at": t, "frame": frame, "measured": False}
        if off > ON_FRAME_TOLERANCE:
            entry["reason"] = (
                f"the cut time {t}s is not on a frame of the delivered video: the nearest is frame {frame} "
                f"({float(frame / out_rate):.6f}s), {off:.3f} frame away"
            )
            cuts.append(entry)
            continue
        value, at, reason = difference(frame)
        if value is None:
            entry["reason"] = reason
            cuts.append(entry)
            continue
        before = difference(frame - 1)[0] if frame > 0 else None
        after = difference(frame + 1)[0]
        entry.update(measured=True, frame_time=at, difference=value, difference_before=before, difference_after=after)
        cuts.append(entry)
    return {"stinger_frame": stinger_frame, "cuts": cuts}


def analyze_stinger(
    ffmpeg_path: str,
    ffprobe_path: str,
    input_path: Path,
    input_probe: Dict[str, Any],
    input_stream: Dict[str, Any],
    rule: Any,
    stinger_path: Optional[Path] = None,
) -> List[QCMeasurement]:
    """The measurements a ``StingerRule`` asks for. Without
    ``stinger_path`` the input is the stinger itself; with it, the input
    is a delivered video and the alpha is read from the stinger file."""

    measurements: List[QCMeasurement] = []
    if stinger_path is None:
        probe_data, stream, failure = input_probe, input_stream, None
        source = "input"
    else:
        probe_data, stream, failure = _media_stream(ffprobe_path, stinger_path)
        source = "stinger_file"
    if failure is not None:
        alpha: Dict[str, Any] = {"source": source, "cut_point": rule.cut_point, "measured": False, "reason": failure}
    else:
        alpha = _alpha_entry(
            ffmpeg_path, ffprobe_path, stinger_path or input_path, probe_data, stream, rule.cut_point, source,
        )
    measurements.append(
        QCMeasurement("video.stinger_cut_point_alpha", "video", "stinger_cut_point_alpha", alpha, source="ffmpeg:extractplanes", notes=ALPHA_NOTE)
    )
    if rule.cut_at:
        if failure is not None or stinger_path is None:
            reason = failure or "no stinger file to compare against"
            frames: Dict[str, Any] = {
                "stinger_frame": {"measured": False, "reason": reason},
                "cuts": [{"cut_at": t, "measured": False, "reason": reason} for t in rule.cut_at],
            }
        else:
            frames = _cut_frames(
                ffmpeg_path, input_path, input_probe, input_stream, stinger_path, probe_data, stream,
                rule.cut_point, list(rule.cut_at),
            )
        measurements.append(
            QCMeasurement("video.stinger_cut_frames", "video", "stinger_cut_frames", frames, source="ffmpeg:rawvideo", notes=CUT_FRAMES_NOTE)
        )
    return measurements


def not_performed(rule: Any, reason: str) -> List[QCMeasurement]:
    """The stinger measurements a rule asks for, each unmeasured with
    ``reason`` (e.g. no ffmpeg on this host)."""

    out = [
        QCMeasurement(
            "video.stinger_cut_point_alpha", "video", "stinger_cut_point_alpha",
            {"source": "stinger_file" if rule.file else "input", "cut_point": rule.cut_point, "measured": False, "reason": reason},
            source="ffmpeg:extractplanes", notes=ALPHA_NOTE,
        )
    ]
    if rule.cut_at:
        out.append(
            QCMeasurement(
                "video.stinger_cut_frames", "video", "stinger_cut_frames",
                {
                    "stinger_frame": {"measured": False, "reason": reason},
                    "cuts": [{"cut_at": t, "measured": False, "reason": reason} for t in rule.cut_at],
                },
                source="ffmpeg:rawvideo", notes=CUT_FRAMES_NOTE,
            )
        )
    return out
