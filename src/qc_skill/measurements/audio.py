"""Audio measurement extraction (STEP 4/5/6 of the task spec).

One ffmpeg pass runs ``astats`` (level/clipping/per-channel stats),
``ebur128`` (integrated loudness, loudness range, true peak) and
``silencedetect`` (silence segments) chained on a single ``-af``, since all
three are pass-through filters and chaining avoids decoding the file three
times. Their *detection* parameters (silence threshold/min-duration) have
documented defaults and can be overridden per request; the *evaluation* of
whether a given loudness/silence measurement is acceptable is a Rule
(rules.py), never decided here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import error
from ..models import QCMeasurement
from ..runner import ffmpeg_analysis_argv, run_argv
from ._decode_errors import extract_decode_errors

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?[\d.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?[\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")

_ASTATS_LINE_RE = re.compile(r"\[Parsed_astats_\d+ @[^\]]*\]\s*(.+)$")
_ASTATS_CHANNEL_RE = re.compile(r"^Channel:\s*(\d+)$")

_INTEGRATED_RE = re.compile(r"\bI:\s*(-?[\d.]+|-inf)\s*LUFS")
_LRA_RE = re.compile(r"\bLRA:\s*([\d.]+)\s*LU\b")
_TRUE_PEAK_RE = re.compile(r"\bPeak:\s*(-?[\d.]+|-inf|inf)\s*dBFS")


def measure_audio_streams(probe_data: Dict[str, Any]) -> List[QCMeasurement]:
    streams = [s for s in probe_data.get("streams", []) if s.get("codec_type") == "audio"]
    measurements: List[QCMeasurement] = [
        QCMeasurement("audio.stream_present", "audio", "stream_present", bool(streams), source="ffprobe"),
        QCMeasurement("audio.stream_count", "audio", "stream_count", len(streams), source="ffprobe"),
    ]
    if not streams:
        return measurements

    stream = streams[0]
    idx = stream.get("index")
    measurements.append(QCMeasurement("audio.codec", "audio", "codec", stream.get("codec_name"), stream=idx, source="ffprobe"))
    sr = stream.get("sample_rate")
    measurements.append(
        QCMeasurement("audio.sample_rate", "audio", "sample_rate", int(sr) if sr is not None else None, unit="Hz", stream=idx, source="ffprobe")
    )
    measurements.append(QCMeasurement("audio.channels", "audio", "channels", stream.get("channels"), stream=idx, source="ffprobe"))
    measurements.append(
        QCMeasurement("audio.channel_layout", "audio", "channel_layout", stream.get("channel_layout"), stream=idx, source="ffprobe")
    )
    duration = stream.get("duration") or probe_data.get("format", {}).get("duration")
    measurements.append(
        QCMeasurement("audio.duration_sec", "audio", "duration_sec", float(duration) if duration is not None else None, unit="sec", stream=idx, source="ffprobe")
    )
    return measurements


def _to_float(text: str) -> Optional[float]:
    text = text.strip()
    if text in ("-inf", "-Infinity"):
        return float("-inf")
    if text in ("inf", "Infinity"):
        return float("inf")
    try:
        return float(text)
    except ValueError:
        return None


def _parse_astats(stderr: str) -> Dict[str, Any]:
    """Return {"channels": {1: {field: value}, ...}, "overall": {field: value}}."""

    channels: Dict[int, Dict[str, Any]] = {}
    overall: Dict[str, Any] = {}
    current: Optional[Dict[str, Any]] = None

    for raw_line in stderr.splitlines():
        m = _ASTATS_LINE_RE.search(raw_line)
        if not m:
            continue
        remainder = m.group(1).strip()

        chan_m = _ASTATS_CHANNEL_RE.match(remainder)
        if chan_m:
            current = channels.setdefault(int(chan_m.group(1)), {})
            continue
        if remainder == "Overall":
            current = overall
            continue
        if ":" not in remainder or current is None:
            continue
        key, _, value = remainder.partition(":")
        key, value = key.strip(), value.strip()
        parsed = _to_float(value)
        current[key] = parsed if parsed is not None else value

    return {"channels": channels, "overall": overall}


def _parse_ebur128_summary(stderr: str) -> Dict[str, Optional[float]]:
    idx = stderr.rfind("Summary:")
    section = stderr[idx:] if idx != -1 else ""
    integrated = None
    lra = None
    true_peak = None

    m = _INTEGRATED_RE.search(section)
    if m:
        integrated = _to_float(m.group(1))
    m = _LRA_RE.search(section)
    if m:
        lra = _to_float(m.group(1))
    m = _TRUE_PEAK_RE.search(section)
    if m:
        true_peak = _to_float(m.group(1))

    return {"integrated_lufs": integrated, "loudness_range_lu": lra, "true_peak_dbfs": true_peak}


def _parse_silence_segments(stderr: str, duration_sec: Optional[float]) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    pending_start: Optional[float] = None

    # Interleave start/end events in the order they appear in stderr.
    tokens = []
    for m in _SILENCE_START_RE.finditer(stderr):
        tokens.append((m.start(), "start", float(m.group(1)), None))
    for m in _SILENCE_END_RE.finditer(stderr):
        tokens.append((m.start(), "end", float(m.group(1)), float(m.group(2))))
    tokens.sort(key=lambda t: t[0])

    for _, kind, value, duration in tokens:
        if kind == "start":
            pending_start = value
        else:
            end = value
            start = pending_start if pending_start is not None else max(end - (duration or 0.0), 0.0)
            events.append({"start": start, "end": end, "duration": duration})
            pending_start = None

    if pending_start is not None:
        end = duration_sec
        events.append(
            {
                "start": pending_start,
                "end": end,
                "duration": (end - pending_start) if end is not None else None,
            }
        )

    epsilon = 0.05
    for seg in events:
        if seg["start"] is not None and seg["start"] <= epsilon:
            seg["position"] = "leading"
        elif duration_sec is not None and seg["end"] is not None and seg["end"] >= duration_sec - epsilon:
            seg["position"] = "trailing"
        elif seg["end"] is None:
            seg["position"] = "trailing"
        else:
            seg["position"] = "internal"

    return events


@dataclass
class AudioAnalysisResult:
    measurements: List[QCMeasurement] = field(default_factory=list)
    performed: bool = False


def analyze_audio(
    ffmpeg_path: str,
    input_path: Path,
    audio_stream_index: int,
    duration_sec: Optional[float],
    *,
    silence_threshold_db: float = -30.0,
    silence_min_duration: float = 0.5,
    clipping_threshold_db: float = -0.1,
    timeout: float = 120.0,
) -> AudioAnalysisResult:
    filters = [
        "astats=metadata=0:reset=0",
        "ebur128=peak=true",
        f"silencedetect=n={silence_threshold_db}dB:d={silence_min_duration}",
    ]
    argv = ffmpeg_analysis_argv(
        ffmpeg_path,
        input_path,
        filters,
        audio=True,
        stream_select=f"0:{audio_stream_index}",
    )
    try:
        result = run_argv(argv, timeout=timeout)
    except FileNotFoundError:
        raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)

    if result.timed_out:
        raise error("TOOL_ERROR", "ffmpeg audio analysis timed out", argv=argv, timeout=timeout)

    astats = _parse_astats(result.stderr)
    loudness = _parse_ebur128_summary(result.stderr)
    silence_segments = _parse_silence_segments(result.stderr, duration_sec)
    decode_errors = extract_decode_errors(result.stderr.splitlines())

    measurements: List[QCMeasurement] = []
    measurements.append(QCMeasurement("audio.decode_error_count", "audio", "decode_error_count", len(decode_errors), source="ffmpeg:decode"))
    measurements.append(QCMeasurement("audio.decode_errors", "audio", "decode_errors", decode_errors[:200], source="ffmpeg:decode"))

    overall = astats["overall"]
    peak_db = overall.get("Peak level dB")
    rms_db = overall.get("RMS level dB")

    measurements.append(QCMeasurement("audio.peak_level_dbfs", "audio", "peak_level_dbfs", peak_db, unit="dBFS", source="ffmpeg:astats"))
    measurements.append(QCMeasurement("audio.rms_level_dbfs", "audio", "rms_level_dbfs", rms_db, unit="dBFS", source="ffmpeg:astats"))
    measurements.append(
        QCMeasurement(
            "audio.clipping_detected", "audio", "clipping_detected",
            bool(peak_db is not None and peak_db >= clipping_threshold_db),
            source="ffmpeg:astats",
            notes=f"heuristic: overall peak level >= {clipping_threshold_db} dBFS",
        )
    )

    per_channel = []
    for chan_num in sorted(astats["channels"]):
        chan = astats["channels"][chan_num]
        per_channel.append(
            {
                "channel": chan_num,
                "peak_level_dbfs": chan.get("Peak level dB"),
                "rms_level_dbfs": chan.get("RMS level dB"),
            }
        )
    measurements.append(QCMeasurement("audio.per_channel_levels", "audio", "per_channel_levels", per_channel, source="ffmpeg:astats"))

    measurements.append(
        QCMeasurement("audio.integrated_loudness_lufs", "audio", "integrated_loudness_lufs", loudness["integrated_lufs"], unit="LUFS", source="ffmpeg:ebur128")
    )
    measurements.append(
        QCMeasurement("audio.loudness_range_lu", "audio", "loudness_range_lu", loudness["loudness_range_lu"], unit="LU", source="ffmpeg:ebur128")
    )
    measurements.append(
        QCMeasurement("audio.true_peak_dbfs", "audio", "true_peak_dbfs", loudness["true_peak_dbfs"], unit="dBFS", source="ffmpeg:ebur128")
    )

    measurements.append(QCMeasurement("audio.silence_segments", "audio", "silence_segments", silence_segments, source="ffmpeg:silencedetect"))
    measurements.append(
        QCMeasurement(
            "audio.leading_silence_sec", "audio", "leading_silence_sec",
            next((s["duration"] for s in silence_segments if s["position"] == "leading"), 0.0),
            unit="sec", source="ffmpeg:silencedetect",
        )
    )
    trailing = [s for s in silence_segments if s["position"] == "trailing"]
    measurements.append(
        QCMeasurement(
            "audio.trailing_silence_sec", "audio", "trailing_silence_sec",
            trailing[-1]["duration"] if trailing else 0.0,
            unit="sec", source="ffmpeg:silencedetect",
        )
    )
    internal = [s for s in silence_segments if s["position"] == "internal"]
    measurements.append(
        QCMeasurement(
            "audio.internal_silence_segments", "audio", "internal_silence_segments", internal,
            source="ffmpeg:silencedetect",
        )
    )

    return AudioAnalysisResult(measurements=measurements, performed=True)
