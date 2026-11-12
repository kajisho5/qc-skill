"""Read a word-timed transcript for the subtitle-vs-speech checks (ADR-023).

The transcript is a JSON document produced by a speech-to-text step
(transcription-skill's transcript format): ``segments`` each carrying
``words: [{text | word, start, end, ...}]``. qc-skill never transcribes
anything itself - it only reads timings and labels that are already in
the file, and reads them tolerantly:

* ``text`` or ``word`` names the spoken word (``text`` wins when both are
  present).
* ``index`` (0-based position in the whole transcript) is used when it is
  an integer; otherwise the word's position in reading order is used. The
  position is counted over every word entry, usable or not, so it lines
  up with ``index`` when the transcript carries one.
* ``pos`` (a coarse part-of-speech label) is read when it is a string and
  lower-cased; nothing is inferred when it is missing.
* ``timing: "interpolated"`` (transcription-skill 0.9.0+: a
  character-share estimate on dictionary-segmented words) marks a word
  whose timing was estimated rather than measured; ``"measured"`` or no
  ``timing`` key means a measured / engine timing. A boolean
  ``interpolated`` / ``timing_interpolated`` / ``estimated`` set to
  ``true`` is read the same way.
* A segment whose ``words`` is ``null`` contributes no words.
* A top-level ``words`` list is read when no segment carries words; a
  top-level JSON list is read as a list of segments (or of words).

A word without usable timing (missing, non-numeric, non-finite, or end
before start) or without text is skipped and counted, never repaired.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models import QCMeasurement

_INTERPOLATION_FLAGS = ("interpolated", "timing_interpolated", "estimated")


@dataclass
class TranscriptReadResult:
    words: List[Dict[str, Any]] = field(default_factory=list)
    skipped_word_count: int = 0
    error: Optional[str] = None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _raw_word_entries(doc: Any) -> Optional[List[Any]]:
    """Every word entry, in reading order, or None when the document has
    no recognisable transcript shape."""

    segments: Optional[List[Any]] = None
    top_words: Optional[List[Any]] = None
    if isinstance(doc, dict):
        if isinstance(doc.get("segments"), list):
            segments = doc["segments"]
        if isinstance(doc.get("words"), list):
            top_words = doc["words"]
    elif isinstance(doc, list):
        if any(isinstance(item, dict) and isinstance(item.get("words"), list) for item in doc):
            segments = doc
        else:
            top_words = doc
    if segments is None and top_words is None:
        return None

    entries: List[Any] = []
    if segments is not None:
        for segment in segments:
            if isinstance(segment, dict) and isinstance(segment.get("words"), list):
                entries.extend(segment["words"])
    if not entries and top_words is not None:
        entries = list(top_words)
    return entries


def read_transcript(path: Path) -> TranscriptReadResult:
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError) as exc:
        return TranscriptReadResult(error=f"transcript could not be read: {exc}")
    except ValueError as exc:
        return TranscriptReadResult(error=f"transcript is not valid JSON: {exc}")

    entries = _raw_word_entries(doc)
    if entries is None:
        return TranscriptReadResult(error="transcript has neither 'segments' nor 'words'")

    result = TranscriptReadResult()
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            result.skipped_word_count += 1
            continue
        text = entry.get("text")
        if not isinstance(text, str):
            text = entry.get("word")
        text = text.strip() if isinstance(text, str) else ""
        start = _number(entry.get("start"))
        end = _number(entry.get("end"))
        if not text or start is None or end is None or end < start:
            result.skipped_word_count += 1
            continue
        index = entry.get("index")
        pos = entry.get("pos")
        result.words.append({
            "position": position,
            "index": index if isinstance(index, int) and not isinstance(index, bool) else None,
            "text": text,
            "start": start,
            "end": end,
            "pos": pos.strip().lower() if isinstance(pos, str) and pos.strip() else None,
            "interpolated": (
                entry.get("timing") == "interpolated"
                or any(entry.get(flag) is True for flag in _INTERPOLATION_FLAGS)
            ),
        })
    return result


def measure_transcript(path: Path) -> List[QCMeasurement]:
    result = read_transcript(path)
    if result.error is not None:
        return [
            QCMeasurement(
                "subtitle.transcript_words", "subtitle", "transcript_words", None,
                source="transcript", notes=result.error,
            ),
            QCMeasurement("subtitle.transcript_word_count", "subtitle", "transcript_word_count", None, source="transcript", notes=result.error),
        ]
    words = result.words
    return [
        QCMeasurement(
            "subtitle.transcript_words", "subtitle", "transcript_words", words, source="transcript",
            notes="timed words in transcript order: position (0-based reading order), index (the transcript's own, or null), "
                  "text, start, end, pos (or null), interpolated",
        ),
        QCMeasurement("subtitle.transcript_word_count", "subtitle", "transcript_word_count", len(words), source="transcript"),
        QCMeasurement(
            "subtitle.transcript_skipped_word_count", "subtitle", "transcript_skipped_word_count", result.skipped_word_count,
            source="transcript", notes="word entries without text or usable start/end timing",
        ),
        QCMeasurement(
            "subtitle.transcript_pos_word_count", "subtitle", "transcript_pos_word_count",
            sum(1 for w in words if w["pos"] is not None), source="transcript",
        ),
        QCMeasurement(
            "subtitle.transcript_interpolated_word_count", "subtitle", "transcript_interpolated_word_count",
            sum(1 for w in words if w["interpolated"]), source="transcript",
        ),
    ]
