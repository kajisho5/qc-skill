"""Subtitle measurement extraction (STEP 7 of the task spec).

Parses SRT, WebVTT and (cue-count/timing only) ASS/SSA files with the
standard library alone. Cue *content* is never evaluated for quality or
correctness - only structural facts (timestamps, counts, line length,
control characters, overlaps, gaps) are measured. Whether a given gap or
line length is acceptable is a Rule (rules.py), not decided here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..models import QCMeasurement

_SRT_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{3})")
_VTT_TIME_RE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})\.(\d{3})")
_ASS_TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})\.(\d{2})")

_TIMING_LINE_RE = re.compile(r"-->")


@dataclass
class Cue:
    index: int  # ordinal position in the file (1-based)
    cue_id: Optional[str]  # explicit identifier (SRT sequence number, or VTT id if present)
    start: Optional[float]
    end: Optional[float]
    lines: List[str] = field(default_factory=list)
    timestamp_error: Optional[str] = None


def detect_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".srt":
        return "srt"
    if suffix == ".vtt":
        return "vtt"
    if suffix in (".ass", ".ssa"):
        return "ass"
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:64].lstrip()
    except OSError:
        return "unknown"
    if head.startswith("WEBVTT"):
        return "vtt"
    if head.startswith("[Script Info]"):
        return "ass"
    if re.match(r"^\d+\s*$", head.splitlines()[0] if head.splitlines() else ""):
        return "srt"
    return "unknown"


def _srt_to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(text: str) -> List[Cue]:
    blocks = re.split(r"\r?\n\r?\n+", text.strip())
    cues: List[Cue] = []
    for i, block in enumerate(blocks, start=1):
        lines = [ln for ln in block.splitlines() if ln != ""]
        if not lines:
            continue
        cue_id = None
        idx = 0
        if re.match(r"^\d+$", lines[0].strip()):
            cue_id = lines[0].strip()
            idx = 1
        if idx >= len(lines) or not _TIMING_LINE_RE.search(lines[idx]):
            cues.append(Cue(index=i, cue_id=cue_id, start=None, end=None, lines=[], timestamp_error="missing timing line"))
            continue
        timing = lines[idx]
        times = _SRT_TIME_RE.findall(timing)
        text_lines = lines[idx + 1:]
        if len(times) < 2:
            cues.append(Cue(index=i, cue_id=cue_id, start=None, end=None, lines=text_lines, timestamp_error="unparsable timestamp"))
            continue
        start = _srt_to_seconds(*times[0])
        end = _srt_to_seconds(*times[1])
        cues.append(Cue(index=i, cue_id=cue_id, start=start, end=end, lines=text_lines))
    return cues


def parse_vtt(text: str) -> List[Cue]:
    body = re.sub(r"^WEBVTT[^\n]*\n", "", text.lstrip(), count=1)
    blocks = re.split(r"\r?\n\r?\n+", body.strip())
    cues: List[Cue] = []
    idx_counter = 0
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln != ""]
        if not lines:
            continue
        if lines[0].upper().startswith(("NOTE", "STYLE", "REGION")):
            continue
        idx_counter += 1
        cue_id = None
        line_idx = 0
        if not _TIMING_LINE_RE.search(lines[0]):
            cue_id = lines[0].strip()
            line_idx = 1
        if line_idx >= len(lines) or not _TIMING_LINE_RE.search(lines[line_idx]):
            cues.append(Cue(index=idx_counter, cue_id=cue_id, start=None, end=None, lines=[], timestamp_error="missing timing line"))
            continue
        timing = lines[line_idx]
        times = _VTT_TIME_RE.findall(timing)
        text_lines = lines[line_idx + 1:]
        if len(times) < 2:
            cues.append(Cue(index=idx_counter, cue_id=cue_id, start=None, end=None, lines=text_lines, timestamp_error="unparsable timestamp"))
            continue

        def to_seconds(groups):
            h, m, s, ms = groups
            h = h or "0"
            return _srt_to_seconds(h, m, s, ms)

        start = to_seconds(times[0])
        end = to_seconds(times[1])
        cues.append(Cue(index=idx_counter, cue_id=cue_id, start=start, end=end, lines=text_lines))
    return cues


def parse_ass(text: str) -> List[Cue]:
    cues: List[Cue] = []
    idx = 0
    for line in text.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        idx += 1
        fields = line[len("Dialogue:"):].split(",", 9)
        if len(fields) < 10:
            cues.append(Cue(index=idx, cue_id=None, start=None, end=None, lines=[], timestamp_error="malformed Dialogue line"))
            continue
        start_raw, end_raw, text_field = fields[1].strip(), fields[2].strip(), fields[9]
        m_start = _ASS_TIME_RE.match(start_raw)
        m_end = _ASS_TIME_RE.match(end_raw)
        if not m_start or not m_end:
            cues.append(Cue(index=idx, cue_id=None, start=None, end=None, lines=[text_field], timestamp_error="unparsable timestamp"))
            continue
        start = _srt_to_seconds(m_start.group(1), m_start.group(2), m_start.group(3), m_start.group(4) + "0")
        end = _srt_to_seconds(m_end.group(1), m_end.group(2), m_end.group(3), m_end.group(4) + "0")
        clean_text = re.sub(r"\{[^}]*\}", "", text_field).replace("\\N", "\n").replace("\\n", "\n")
        cues.append(Cue(index=idx, cue_id=None, start=start, end=end, lines=clean_text.split("\n")))
    return cues


def parse_subtitle_file(path: Path) -> "SubtitleParseResult":
    fmt = detect_format(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if fmt == "srt":
        cues = parse_srt(text)
    elif fmt == "vtt":
        cues = parse_vtt(text)
    elif fmt == "ass":
        cues = parse_ass(text)
    else:
        cues = []
    return SubtitleParseResult(format=fmt, cues=cues)


@dataclass
class SubtitleParseResult:
    format: str
    cues: List[Cue]


_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def measure_subtitle(
    path: Path,
    video_duration_sec: Optional[float] = None,
    *,
    max_line_length: int = 42,
    max_cue_duration: float = 7.0,
    max_gap_sec: float = 5.0,
) -> List[QCMeasurement]:
    parsed = parse_subtitle_file(path)
    cues = parsed.cues

    measurements: List[QCMeasurement] = [
        QCMeasurement("subtitle.exists", "subtitle", "exists", True, source="OBSERVED"),
        QCMeasurement("subtitle.format", "subtitle", "format", parsed.format, source="OBSERVED"),
        QCMeasurement("subtitle.cue_count", "subtitle", "cue_count", len(cues), source="OBSERVED"),
        QCMeasurement(
            "subtitle.cues", "subtitle", "cues",
            [{"index": c.index, "start": c.start, "end": c.end} for c in cues],
            source="OBSERVED", notes="raw parsed cue timing, in file order - start/end are null for a cue with a timestamp error",
        ),
    ]

    invalid_timestamps = []
    for cue in cues:
        if cue.timestamp_error:
            invalid_timestamps.append({"index": cue.index, "reason": cue.timestamp_error})
        elif cue.start is not None and cue.end is not None and cue.start > cue.end:
            invalid_timestamps.append({"index": cue.index, "reason": "start after end", "start": cue.start, "end": cue.end})
        elif cue.start is not None and cue.start < 0:
            invalid_timestamps.append({"index": cue.index, "reason": "negative start", "start": cue.start})
    measurements.append(QCMeasurement("subtitle.invalid_timestamps", "subtitle", "invalid_timestamps", invalid_timestamps, source="OBSERVED"))

    valid_cues = sorted(
        (c for c in cues if c.start is not None and c.end is not None and c.start <= c.end),
        key=lambda c: c.start,
    )

    overlaps = []
    for a, b in zip(valid_cues, valid_cues[1:]):
        if a.end > b.start:
            overlaps.append({"a_index": a.index, "b_index": b.index, "overlap_sec": a.end - b.start})
    measurements.append(QCMeasurement("subtitle.overlapping_cues", "subtitle", "overlapping_cues", overlaps, source="OBSERVED"))

    empty_cues = [c.index for c in cues if not any(line.strip() for line in c.lines)]
    measurements.append(QCMeasurement("subtitle.empty_cues", "subtitle", "empty_cues", empty_cues, source="OBSERVED"))

    ids = [c.cue_id for c in cues if c.cue_id is not None]
    seen = set()
    duplicate_ids = sorted({i for i in ids if i in seen or seen.add(i)})
    measurements.append(QCMeasurement("subtitle.duplicate_ids", "subtitle", "duplicate_ids", duplicate_ids, source="OBSERVED"))

    control_char_cues = []
    for c in cues:
        for line in c.lines:
            if _CONTROL_CHARS_RE.search(line):
                control_char_cues.append(c.index)
                break
    measurements.append(
        QCMeasurement("subtitle.invalid_control_characters", "subtitle", "invalid_control_characters", control_char_cues, source="OBSERVED")
    )

    subtitle_duration = max((c.end for c in valid_cues), default=0.0)
    measurements.append(QCMeasurement("subtitle.duration_sec", "subtitle", "duration_sec", subtitle_duration, unit="sec", source="OBSERVED"))

    if video_duration_sec is not None and video_duration_sec > 0:
        covered = sum((c.end - c.start) for c in valid_cues)
        measurements.append(
            QCMeasurement(
                "subtitle.coverage_ratio", "subtitle", "coverage_ratio",
                round(covered / video_duration_sec, 4), source="ffprobe+OBSERVED",
                notes="sum of cue durations divided by video duration; overlaps double-count",
            )
        )
        measurements.append(
            QCMeasurement(
                "subtitle.duration_delta_sec", "subtitle", "duration_delta_sec",
                round(video_duration_sec - subtitle_duration, 3), unit="sec", source="ffprobe+OBSERVED",
            )
        )

    duration_minutes = subtitle_duration / 60.0 if subtitle_duration else 0.0
    density = round(len(valid_cues) / duration_minutes, 3) if duration_minutes > 0 else None
    measurements.append(QCMeasurement("subtitle.cue_density_per_min", "subtitle", "cue_density_per_min", density, source="OBSERVED"))

    excessive_length = []
    for c in cues:
        longest = max((len(line) for line in c.lines), default=0)
        if longest > max_line_length:
            excessive_length.append({"index": c.index, "length": longest})
    measurements.append(QCMeasurement("subtitle.excessive_line_length", "subtitle", "excessive_line_length", excessive_length, source="OBSERVED"))

    excessive_duration = [
        {"index": c.index, "duration": round(c.end - c.start, 3)}
        for c in valid_cues
        if (c.end - c.start) > max_cue_duration
    ]
    measurements.append(QCMeasurement("subtitle.excessive_cue_duration", "subtitle", "excessive_cue_duration", excessive_duration, source="OBSERVED"))

    gaps = []
    prev_end = 0.0
    for c in valid_cues:
        if c.start - prev_end > max_gap_sec:
            gaps.append({"start": prev_end, "end": c.start, "duration": round(c.start - prev_end, 3)})
        prev_end = max(prev_end, c.end)
    if video_duration_sec is not None and video_duration_sec - prev_end > max_gap_sec:
        gaps.append({"start": prev_end, "end": video_duration_sec, "duration": round(video_duration_sec - prev_end, 3)})
    measurements.append(QCMeasurement("subtitle.gaps", "subtitle", "gaps", gaps, source="OBSERVED", notes="gaps longer than the detection threshold with no active cue"))

    return measurements
