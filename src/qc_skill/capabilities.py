"""Engine/dependency capability detection for doctor and contract.

Three-state model (never collapse "couldn't tell" into "present" or
"absent"): a capability is ``available``, ``missing``, or ``unknown``
(detection itself failed to parse). doctor must never report a check as
AVAILABLE unless it actually verified the capability.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, Optional, Set

from .runner import run_argv

REQUIRED_FILTERS = ("silencedetect", "blackdetect", "freezedetect", "ebur128", "astats")
# Full-reference metric filters (ADR-021). Optional: only an opt-in
# VideoRule.reference_metrics request uses them, so their absence never
# degrades doctor. libvmaf exists only in an ffmpeg configured with
# --enable-libvmaf; without it VMAF is UNKNOWN while SSIM/PSNR still run.
REFERENCE_METRIC_FILTERS = ("libvmaf", "ssim", "psnr")

# One `ffmpeg -filters` row: " TS. ssim   VV->V   Calculate the SSIM ..."
_FILTER_ROW_RE = re.compile(r"^\s*[A-Z.|]{2,4}\s+([A-Za-z0-9_]+)\s+\S*->\S*\s")


@dataclass
class CapabilitySet:
    ffmpeg_path: str = ""
    ffprobe_path: str = ""
    ffmpeg_version: str = ""
    ffprobe_version: str = ""
    filters: Dict[str, str] = field(default_factory=dict)  # name -> available|missing|unknown
    # `ffmpeg -buildconf` names --enable-libvmaf: True/False, None = could not be read
    buildconf_libvmaf: Optional[bool] = None

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


def listed_filter_names(filters_stdout: str) -> Set[str]:
    """Exact filter names from ``ffmpeg -filters`` (a substring test would
    take ``libvmaf_cuda`` or ``ssim360`` for ``libvmaf``/``ssim``)."""

    names = set()
    for line in filters_stdout.splitlines():
        m = _FILTER_ROW_RE.match(line)
        if m:
            names.add(m.group(1))
    return names


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
            names = listed_filter_names(listed)
            for name in REFERENCE_METRIC_FILTERS:
                caps.filters[name] = "available" if name in names else ("missing" if names else "unknown")
        else:
            for name in REQUIRED_FILTERS + REFERENCE_METRIC_FILTERS:
                caps.filters[name] = "unknown"
        result = run_argv([caps.ffmpeg_path, "-hide_banner", "-buildconf"], timeout=15)
        if result.returncode == 0 and "configuration" in result.stdout.lower():
            caps.buildconf_libvmaf = "--enable-libvmaf" in result.stdout
    else:
        for name in REQUIRED_FILTERS + REFERENCE_METRIC_FILTERS:
            caps.filters[name] = "unknown"

    return caps
