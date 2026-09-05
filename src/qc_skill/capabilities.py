"""Engine/dependency capability detection for doctor and contract.

Three-state model (never collapse "couldn't tell" into "present" or
"absent"): a capability is ``available``, ``missing``, or ``unknown``
(detection itself failed to parse). doctor must never report a check as
AVAILABLE unless it actually verified the capability.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from typing import Dict

from .runner import run_argv

REQUIRED_FILTERS = ("silencedetect", "blackdetect", "freezedetect", "ebur128", "astats")


@dataclass
class CapabilitySet:
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    ffmpeg_version: str = ""
    ffprobe_version: str = ""
    filters: Dict[str, str] = field(default_factory=dict)  # name -> available|missing|unknown

    @property
    def ffmpeg_available(self) -> bool:
        return bool(self.ffmpeg_path)

    @property
    def ffprobe_available(self) -> bool:
        return bool(self.ffprobe_path)

    def filter_available(self, name: str) -> bool:
        return self.filters.get(name) == "available"


def _version_line(stdout: str) -> str:
    first = stdout.splitlines()[0] if stdout.splitlines() else ""
    # "ffmpeg version 6.1.1-3ubuntu5 Copyright ..." -> "6.1.1-3ubuntu5"
    parts = first.split()
    if len(parts) >= 3 and parts[0] in ("ffmpeg", "ffprobe"):
        return parts[2]
    return first.strip()


def detect_capabilities() -> CapabilitySet:
    caps = CapabilitySet()

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        result = run_argv([ffmpeg_path, "-hide_banner", "-version"], timeout=15)
        if result.returncode == 0:
            caps.ffmpeg_path = ffmpeg_path
            caps.ffmpeg_version = _version_line(result.stdout)

    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path:
        result = run_argv([ffprobe_path, "-hide_banner", "-version"], timeout=15)
        if result.returncode == 0:
            caps.ffprobe_path = ffprobe_path
            caps.ffprobe_version = _version_line(result.stdout)

    if caps.ffmpeg_available:
        result = run_argv([caps.ffmpeg_path, "-hide_banner", "-filters"], timeout=15)
        if result.returncode == 0:
            listed = result.stdout
            for name in REQUIRED_FILTERS:
                caps.filters[name] = "available" if name in listed else "missing"
        else:
            for name in REQUIRED_FILTERS:
                caps.filters[name] = "unknown"
    else:
        for name in REQUIRED_FILTERS:
            caps.filters[name] = "unknown"

    return caps
