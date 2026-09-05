"""Shared decoder-error line classification (used by video.py and audio.py)."""

from __future__ import annotations

import re
from typing import Dict, List

_ERROR_CATEGORY_PATTERNS = [
    ("missing_reference", re.compile(r"missing reference|reference picture missing", re.I)),
    (
        "corrupt_data",
        re.compile(
            r"invalid nal unit|invalid data found|corrupt decoded frame|"
            r"error splitting the input|missing picture in access unit|"
            r"error submitting packet to decoder",
            re.I,
        ),
    ),
    ("concealment", re.compile(r"concealing \d+ .* errors", re.I)),
    ("timestamp", re.compile(r"non-monotonic|invalid timestamp|dts.*less than", re.I)),
]

_TRIGGER_TOKENS = (
    "invalid nal", "invalid data found", "corrupt decoded frame",
    "missing reference", "reference picture missing", "missing picture in access unit",
    "error splitting the input", "error submitting packet to decoder",
    "concealing", "mmco:", "non-monotonic", "invalid timestamp",
)


def classify_error_line(line: str) -> str:
    for category, pattern in _ERROR_CATEGORY_PATTERNS:
        if pattern.search(line):
            return category
    return "other"


def extract_decode_errors(stderr_lines: List[str]) -> List[Dict[str, str]]:
    errors = []
    for line in stderr_lines:
        low = line.lower()
        if any(token in low for token in _TRIGGER_TOKENS):
            errors.append({"text": line.strip(), "category": classify_error_line(line)})
    return errors
