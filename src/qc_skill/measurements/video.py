"""Video measurement extraction (STEP 3 of the task spec).

Everything here returns ``QCMeasurement`` objects - structured facts, never
raw ffprobe/ffmpeg text. Detection filters (blackdetect/freezedetect) have
their own *detection sensitivity* parameters (threshold, minimum duration)
with documented defaults; those are not the same thing as a policy rule
("how much total black time is acceptable") - see rules.py and STEP 9/10.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import error
from ..models import QCMeasurement
from ..probe import parse_frame_rate
from ..runner import ffmpeg_analysis_argv, run_argv
from ._decode_errors import extract_decode_errors

_BLACK_RE = re.compile(
    r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+black_duration:\s*([\d.]+)"
)
_FREEZE_START_RE = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*([\d.]+)")
_FREEZE_DURATION_RE = re.compile(r"lavfi\.freezedetect\.freeze_duration:\s*([\d.]+)")
_FREEZE_END_RE = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*([\d.]+)")
_FRAME_PROGRESS_RE = re.compile(r"frame=\s*(\d+)")
_FRAME_HEADER_RE = re.compile(r"^frame:\d+\s+pts:-?\d+\s+pts_time:(-?[\d.]+)")
_YMIN_RE = re.compile(r"^lavfi\.signalstats\.YMIN=(\d+)")
_YMAX_RE = re.compile(r"^lavfi\.signalstats\.YMAX=(\d+)")


def _gcd_ratio(width: int, height: int) -> str:
    g = math.gcd(width, height) or 1
    return f"{width // g}:{height // g}"


def measure_container(probe_data: Dict[str, Any], actual_size_bytes: Optional[int] = None) -> List[QCMeasurement]:
    """``actual_size_bytes``, when given, is the caller's own ``stat()`` of
    the resolved input file - authoritative, always available, and
    preferred over ffprobe's self-reported ``format.size`` (which some
    containers/formats omit).
    """

    fmt = probe_data.get("format", {})
    measurements: List[QCMeasurement] = []

    measurements.append(
        QCMeasurement(
            id="container.format_name",
            category="container",
            name="format_name",
            value=fmt.get("format_name"),
            source="ffprobe",
        )
    )
    duration = fmt.get("duration")
    measurements.append(
        QCMeasurement(
            id="container.duration_sec",
            category="container",
            name="duration_sec",
            value=float(duration) if duration is not None else None,
            unit="sec",
            source="ffprobe",
        )
    )
    if actual_size_bytes is not None:
        measurements.append(
            QCMeasurement(
                id="container.size_bytes", category="container", name="size_bytes",
                value=actual_size_bytes, unit="bytes", source="OBSERVED",
                notes="from the resolved input file's own stat(), not the container's self-reported size",
            )
        )
    else:
        size = fmt.get("size")
        measurements.append(
            QCMeasurement(
                id="container.size_bytes",
                category="container",
                name="size_bytes",
                value=int(size) if size is not None else None,
                unit="bytes",
                source="ffprobe",
            )
        )
    bit_rate = fmt.get("bit_rate")
    measurements.append(
        QCMeasurement(
            id="container.bit_rate",
            category="container",
            name="bit_rate",
            value=int(bit_rate) if bit_rate is not None else None,
            unit="bps",
            source="ffprobe",
        )
    )
    return measurements


def measure_video_streams(probe_data: Dict[str, Any]) -> List[QCMeasurement]:
    streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "video"]
    measurements: List[QCMeasurement] = [
        QCMeasurement(
            id="video.stream_present",
            category="video",
            name="stream_present",
            value=bool(streams),
            source="ffprobe",
        ),
        QCMeasurement(
            id="video.stream_count",
            category="video",
            name="stream_count",
            value=len(streams),
            source="ffprobe",
        ),
    ]
    if not streams:
        return measurements

    stream = streams[0]
    idx = stream.get("index")
    width = stream.get("width")
    height = stream.get("height")

    measurements.append(QCMeasurement("video.codec", "video", "codec", stream.get("codec_name"), stream=idx, source="ffprobe"))
    measurements.append(QCMeasurement("video.width", "video", "width", width, unit="px", stream=idx, source="ffprobe"))
    measurements.append(QCMeasurement("video.height", "video", "height", height, unit="px", stream=idx, source="ffprobe"))

    dar = stream.get("display_aspect_ratio")
    if dar and dar != "0:1":
        aspect_value, aspect_source, estimated = dar, "ffprobe", False
    elif width and height:
        aspect_value, aspect_source, estimated = _gcd_ratio(int(width), int(height)), "ffprobe:derived", True
    else:
        aspect_value, aspect_source, estimated = None, "ffprobe", False
    measurements.append(
        QCMeasurement("video.aspect_ratio", "video", "aspect_ratio", aspect_value, stream=idx, source=aspect_source, estimated=estimated)
    )

    avg_fps = parse_frame_rate(stream.get("avg_frame_rate"))
    r_fps = parse_frame_rate(stream.get("r_frame_rate"))
    fps = avg_fps or r_fps
    measurements.append(QCMeasurement("video.frame_rate", "video", "frame_rate", fps, unit="fps", stream=idx, source="ffprobe"))

    # Mirrors ffmpeg-skill's variable_frame_rate_suspected heuristic: a
    # container's declared r_frame_rate (the least-common-multiple rate the
    # timebase could represent) diverging from its avg_frame_rate (frame
    # count / duration) suggests inter-frame timing isn't actually constant.
    # None (not False) when either rate could not be measured - never a
    # guessed "not VFR".
    vfr_suspected = None
    if r_fps is not None and avg_fps is not None:
        vfr_suspected = abs(r_fps - avg_fps) > 0.01
    measurements.append(
        QCMeasurement(
            "video.variable_frame_rate_suspected", "video", "variable_frame_rate_suspected", vfr_suspected,
            stream=idx, source="ffprobe:derived",
            notes="derived from |r_frame_rate - avg_frame_rate| > 0.01fps; null when either rate is unavailable",
        )
    )

    nb_frames = stream.get("nb_frames")
    if nb_frames is not None:
        measurements.append(
            QCMeasurement("video.frame_count", "video", "frame_count", int(nb_frames), stream=idx, source="ffprobe")
        )
    else:
        duration = stream.get("duration") or probe_data.get("format", {}).get("duration")
        if duration is not None and fps:
            estimate = round(float(duration) * fps)
            measurements.append(
                QCMeasurement(
                    "video.frame_count", "video", "frame_count", estimate,
                    stream=idx, source="ffprobe:derived", estimated=True,
                    notes="derived from duration * frame_rate; nb_frames was not reported by the container",
                )
            )
        else:
            measurements.append(
                QCMeasurement(
                    "video.frame_count", "video", "frame_count", None,
                    stream=idx, source="ffprobe", notes="not available: no nb_frames and insufficient data to derive it",
                )
            )

    measurements.append(QCMeasurement("video.pixel_format", "video", "pixel_format", stream.get("pix_fmt"), stream=idx, source="ffprobe"))

    for field_name in ("color_range", "color_space", "color_transfer", "color_primaries", "field_order"):
        measurements.append(
            QCMeasurement(f"video.{field_name}", "video", field_name, stream.get(field_name), stream=idx, source="ffprobe")
        )

    return measurements


def measure_audio_stream_presence(probe_data: Dict[str, Any]) -> List[QCMeasurement]:
    streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]
    return [
        QCMeasurement("audio.stream_present", "audio", "stream_present", bool(streams), source="ffprobe"),
        QCMeasurement("audio.stream_count", "audio", "stream_count", len(streams), source="ffprobe"),
    ]


@dataclass
class VideoDefectResult:
    measurements: List[QCMeasurement] = field(default_factory=list)
    performed: bool = False
    skip_reason: Optional[str] = None


def _parse_luminance_frames(stdout: str) -> List[Dict[str, Any]]:
    """One entry per frame: ``{pts_time, ymin, ymax}``, from
    ``signalstats,metadata=print:file=-`` (written to stdout, never
    stderr, so it never interferes with the blackdetect/freezedetect/
    decode-error parsing above, which all read stderr).
    """

    frames: List[Dict[str, Any]] = []
    pts_time: Optional[float] = None
    ymin: Optional[int] = None
    ymax: Optional[int] = None

    def _flush() -> None:
        if pts_time is not None and ymin is not None and ymax is not None:
            frames.append({"pts_time": pts_time, "ymin": ymin, "ymax": ymax})

    for line in stdout.splitlines():
        m = _FRAME_HEADER_RE.match(line)
        if m:
            _flush()
            pts_time, ymin, ymax = float(m.group(1)), None, None
            continue
        m = _YMIN_RE.match(line)
        if m:
            ymin = int(m.group(1))
            continue
        m = _YMAX_RE.match(line)
        if m:
            ymax = int(m.group(1))
            continue
    _flush()
    return frames


def _build_luminance_excursions(frames: List[Dict[str, Any]], legal_min: int, legal_max: int) -> List[Dict[str, Any]]:
    """Contiguous runs of frames whose Y value fell outside
    ``[legal_min, legal_max]``, merged into segments the same way
    ``black_segments``/``freeze_segments`` already are (ADR-013).
    """

    segments: List[Dict[str, Any]] = []
    seg_start: Optional[float] = None
    seg_min: Optional[int] = None
    seg_max: Optional[int] = None
    last_time: Optional[float] = None

    for f in frames:
        if f["ymin"] < legal_min or f["ymax"] > legal_max:
            if seg_start is None:
                seg_start, seg_min, seg_max = f["pts_time"], f["ymin"], f["ymax"]
            else:
                seg_min, seg_max = min(seg_min, f["ymin"]), max(seg_max, f["ymax"])
            last_time = f["pts_time"]
        elif seg_start is not None:
            segments.append({"start": seg_start, "end": last_time, "duration": last_time - seg_start, "min_y": seg_min, "max_y": seg_max})
            seg_start = None
    if seg_start is not None:
        segments.append({"start": seg_start, "end": last_time, "duration": last_time - seg_start, "min_y": seg_min, "max_y": seg_max})
    return segments


def analyze_video_defects(
    ffmpeg_path: str,
    input_path: Path,
    video_stream_index: int,
    expected_frame_count: Optional[int],
    *,
    black_min_duration: float = 0.5,
    black_pixel_threshold: float = 0.10,
    freeze_noise_db: float = -60.0,
    freeze_min_duration: float = 1.0,
    luminance_legal_min: int = 16,
    luminance_legal_max: int = 235,
    timeout: float = 120.0,
) -> VideoDefectResult:
    """One full decode pass: black-frame + freeze-frame + integrity +
    luminance-range excursions.

    All of these are derived from the same ffmpeg invocation (decode to
    null with blackdetect+freezedetect+signalstats on the video filter
    chain, log level 'info' so filter reports and decoder error/warning
    lines are both captured) to avoid decoding the file more than once
    (ADR-006). ``signalstats,metadata=print:file=-`` writes to *stdout*
    specifically so it never mixes with the stderr-based parsing below.
    """

    filters = [
        f"blackdetect=d={black_min_duration}:pix_th={black_pixel_threshold}",
        f"freezedetect=n={freeze_noise_db}dB:d={freeze_min_duration}",
        "signalstats",
        "metadata=print:file=-",
    ]
    argv = ffmpeg_analysis_argv(
        ffmpeg_path,
        input_path,
        filters,
        audio=False,
        stream_select=f"0:{video_stream_index}",
    )
    # -stats forces periodic frame progress lines even at a low log level.
    argv = argv[:3] + ["-stats"] + argv[3:]

    try:
        result = run_argv(argv, timeout=timeout)
    except FileNotFoundError:
        raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)

    if result.timed_out:
        raise error("TOOL_ERROR", "ffmpeg video defect analysis timed out", argv=argv, timeout=timeout)

    lines = result.stderr.splitlines()

    black_segments = []
    for match in _BLACK_RE.finditer(result.stderr):
        start, end, duration = (float(x) for x in match.groups())
        black_segments.append({"start": start, "end": end, "duration": duration})

    freeze_segments = []
    pending_start: Optional[float] = None
    for line in lines:
        m = _FREEZE_START_RE.search(line)
        if m:
            pending_start = float(m.group(1))
            continue
        m = _FREEZE_DURATION_RE.search(line)
        if m and pending_start is not None:
            dur = float(m.group(1))
            freeze_segments.append({"start": pending_start, "end": pending_start + dur, "duration": dur})
            pending_start = None
            continue
        m = _FREEZE_END_RE.search(line)
        if m and pending_start is not None:
            end = float(m.group(1))
            freeze_segments.append({"start": pending_start, "end": end, "duration": end - pending_start})
            pending_start = None
    if pending_start is not None:
        # Stream ended while still frozen: duration is unknown-but-ongoing;
        # report what we do know rather than fabricating an end time.
        freeze_segments.append({"start": pending_start, "end": None, "duration": None})

    decoded_frame_count = None
    for match in _FRAME_PROGRESS_RE.finditer(result.stderr):
        n = int(match.group(1))
        decoded_frame_count = n if decoded_frame_count is None else max(decoded_frame_count, n)

    error_lines = extract_decode_errors(lines)

    luminance_frames = _parse_luminance_frames(result.stdout)
    luminance_excursions = _build_luminance_excursions(luminance_frames, luminance_legal_min, luminance_legal_max)

    measurements = [
        QCMeasurement("video.black_segments", "video", "black_segments", black_segments, source="ffmpeg:blackdetect"),
        QCMeasurement("video.freeze_segments", "video", "freeze_segments", freeze_segments, source="ffmpeg:freezedetect"),
        QCMeasurement(
            "video.luminance_excursions", "video", "luminance_excursions", luminance_excursions,
            source="ffmpeg:signalstats",
            notes=f"Y outside [{luminance_legal_min}, {luminance_legal_max}] (8-bit); a single-frame excursion reports duration 0.0, not its display duration",
        ),
        QCMeasurement(
            "video.decoded_frame_count", "video", "decoded_frame_count", decoded_frame_count,
            source="ffmpeg:decode",
        ),
        QCMeasurement(
            "video.decode_error_count", "video", "decode_error_count", len(error_lines),
            source="ffmpeg:decode",
        ),
        QCMeasurement(
            "video.decode_errors", "video", "decode_errors", error_lines[:200],
            source="ffmpeg:decode",
            notes="truncated to first 200 lines" if len(error_lines) > 200 else None,
        ),
    ]
    if expected_frame_count is not None and decoded_frame_count is not None:
        measurements.append(
            QCMeasurement(
                "video.frame_count_delta", "video", "frame_count_delta",
                expected_frame_count - decoded_frame_count,
                source="ffmpeg:decode",
                notes="container-reported frame count minus frames actually decoded",
            )
        )

    return VideoDefectResult(measurements=measurements, performed=True)
