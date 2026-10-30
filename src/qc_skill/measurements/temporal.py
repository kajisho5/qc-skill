"""Opt-in temporal analysis pass: photosensitive flashes (ITU-R BT.1702)
and scene-cut times (ffmpeg ``scdet``) - ADR-016 / ADR-017.

Both are produced by one extra decode of the video stream, run only when
a ``VideoRule`` asks for them (``check_photosensitivity`` /
``expected_cuts``), so a request that asks for neither still decodes the
file exactly once (ADR-006). Pure Python + ffmpeg: no numpy, no new
dependency.

Flash measurement (ADR-016), stated as assumptions, not hidden in code:

  * Display mapping: ITU-R BT.1702-3 Annex 2 / Table 1 for SDR - peak
    white = 200 cd/m2 and screen luminance L = 200 * V ** 2.4 cd/m2
    (Rec. ITU-R BT.1886 with a zero black level), V = (Y - 16) / 219 for
    8-bit limited-range luma. Full-range input is converted to limited
    range by ffmpeg (``format=yuv444p``) before Y is read. HDR (PQ/HLG)
    transfer is not mapped: the measurement is ``null`` (UNKNOWN), never
    a guess.
  * Spatial sampling: the luma plane is area-averaged by ffmpeg onto a
    ``FLASH_GRID x FLASH_GRID`` grid; every cell is an equal fraction of
    the displayed screen area. A cell's luminance is computed from its
    area-averaged code value (averaging in the gamma domain) - exact for
    uniform cells, an approximation for cells straddling an edge.
  * A luminance *change* in a cell is an opposing excursion of >= 20
    cd/m2 whose darker side is below 160 cd/m2 (BT.1702-3 Guideline 1,
    SDR branch); a *flash* is a pair of opposing changes, so
    flashes = changes / 2.
  * Area: a 1-second window's area-qualified change count is the largest
    count c such that cells with >= c changes in that window cover more
    than ``flash_area_fraction`` (default 0.25, BT.1702) of the screen.
  * Red flash ("a transition to or from a saturated red") is NOT
    evaluated: BT.1702-3 itself says (Annex 3) that more explicit
    definitions are still required for saturated red, so there is no
    precise definition to implement. ``video.red_flash_count`` is always
    ``null`` with that reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..errors import error
from ..models import QCMeasurement
from ..runner import ffmpeg_rawvideo_argv, run_argv

# --- BT.1702-3 constants (Guideline 1 + Annex 2), not tunable policy ---
SDR_PEAK_WHITE_CD_M2 = 200.0
SDR_DISPLAY_GAMMA = 2.4
FLASH_MIN_DELTA_CD_M2 = 20.0
FLASH_DARKER_BELOW_CD_M2 = 160.0
FLASH_GRID = 32
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}

_SHOWINFO_RE = re.compile(r"Parsed_showinfo.*?\bn:\s*(\d+)\s+pts:\s*-?\d+\s+pts_time:\s*(-?[\d.]+)")
_META_FRAME_RE = re.compile(r"Parsed_metadata.*?frame:(\d+)\s+pts:\s*-?\d+\s+pts_time:\s*(-?[\d.]+)")
_META_SCD_TIME_RE = re.compile(r"Parsed_metadata.*?lavfi\.scd\.time=(-?[\d.]+)")
_SCDET_SCORE_RE = re.compile(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*(-?[\d.]+)")

RED_FLASH_NOTE = (
    "UNKNOWN - not evaluated: ITU-R BT.1702-3 says a transition to or from a saturated red is "
    "potentially harmful but gives no colorimetric definition of 'saturated red' (Annex 3: more "
    "explicit definitions and detection criteria will be required), so qc-skill does not invent one"
)

FLASH_ASSUMPTION_NOTE = (
    "ITU-R BT.1702-3 general flash, SDR: L = 200 * V^2.4 cd/m2 (Annex 2, BT.1886, 200 cd/m2 peak white), "
    "V = (Y-16)/219 on 8-bit limited-range luma; change = opposing excursion >= 20 cd/m2 with the darker "
    f"side < 160 cd/m2; flashes = changes/2; {FLASH_GRID}x{FLASH_GRID} area-averaged grid; "
    "area-qualified = cells with at least that many changes cover more than flash_area_fraction of the screen"
)


def _luminance_lut() -> List[float]:
    lut = []
    for y in range(256):
        v = min(1.0, max(0.0, (y - 16) / 219.0))
        lut.append(SDR_PEAK_WHITE_CD_M2 * (v ** SDR_DISPLAY_GAMMA))
    return lut


_LUT = _luminance_lut()


@dataclass
class TemporalResult:
    measurements: List[QCMeasurement] = field(default_factory=list)


def _cell_change_frames(frames: List[bytes]) -> List[List[int]]:
    """Per grid cell, the frame indices at which a BT.1702 luminance
    change completed. A deterministic peak/trough tracker: after a
    change, follow the running extreme in the same direction, and record
    the next change once luminance has moved >= 20 cd/m2 back the other
    way with the darker side below 160 cd/m2.
    """

    if not frames:
        return []
    n_cells = len(frames[0])
    changes: List[List[int]] = [[] for _ in range(n_cells)]
    lut = _LUT
    first = frames[0]
    direction = [0] * n_cells  # 0 = none yet, +1 = last change was up, -1 = down
    lo = [lut[first[c]] for c in range(n_cells)]
    hi = list(lo)
    for fi in range(1, len(frames)):
        frame = frames[fi]
        for c in range(n_cells):
            lum = lut[frame[c]]
            d = direction[c]
            if d == 0:
                if lum - lo[c] >= FLASH_MIN_DELTA_CD_M2 and lo[c] < FLASH_DARKER_BELOW_CD_M2:
                    changes[c].append(fi)
                    direction[c] = 1
                    hi[c] = lum
                elif hi[c] - lum >= FLASH_MIN_DELTA_CD_M2 and lum < FLASH_DARKER_BELOW_CD_M2:
                    changes[c].append(fi)
                    direction[c] = -1
                    lo[c] = lum
                else:
                    if lum < lo[c]:
                        lo[c] = lum
                    if lum > hi[c]:
                        hi[c] = lum
            elif d == 1:
                if lum > hi[c]:
                    hi[c] = lum
                elif hi[c] - lum >= FLASH_MIN_DELTA_CD_M2 and lum < FLASH_DARKER_BELOW_CD_M2:
                    changes[c].append(fi)
                    direction[c] = -1
                    lo[c] = lum
            else:
                if lum < lo[c]:
                    lo[c] = lum
                elif lum - lo[c] >= FLASH_MIN_DELTA_CD_M2 and lo[c] < FLASH_DARKER_BELOW_CD_M2:
                    changes[c].append(fi)
                    direction[c] = 1
                    hi[c] = lum
    return changes


def _qualified_count(hist: Dict[int, int], needed_cells: int) -> Tuple[int, int]:
    """Largest change count c such that at least ``needed_cells`` cells
    have >= c changes; also returns how many cells reach that c."""

    cumulative = 0
    for count in sorted(hist, reverse=True):
        if count <= 0:
            break
        cumulative += hist[count]
        if cumulative >= needed_cells:
            return count, cumulative
    return 0, 0


def _window_counts(
    events: List[Tuple[float, int]], n_cells: int, needed_cells: int, starts: List[Tuple[float, float]]
) -> List[Tuple[int, int]]:
    """For each [start, end) window, (area-qualified change count, cells
    reaching it). ``events`` are (time, cell) sorted by time; windows are
    sorted by start and end. Two-pointer sliding window, O(events + windows)."""

    counts = [0] * n_cells
    hist: Dict[int, int] = {0: n_cells}
    lo_i = hi_i = 0
    out: List[Tuple[int, int]] = []
    eps = 1e-6

    def _bump(cell: int, delta: int) -> None:
        old = counts[cell]
        hist[old] -= 1
        if hist[old] == 0 and old != 0:
            del hist[old]
        new = old + delta
        counts[cell] = new
        hist[new] = hist.get(new, 0) + 1

    for start, end in starts:
        while hi_i < len(events) and events[hi_i][0] < end - eps:
            _bump(events[hi_i][1], 1)
            hi_i += 1
        while lo_i < hi_i and events[lo_i][0] < start - eps:
            _bump(events[lo_i][1], -1)
            lo_i += 1
        out.append(_qualified_count(hist, needed_cells))
    return out


def analyze_flashes(frames: List[bytes], times: List[float], area_fraction: float) -> Dict[str, Any]:
    """Pure function over grid frames -> per-second and worst-window
    area-qualified flash counts. Exposed for unit tests."""

    if not frames:
        return {"per_second": [], "worst_window": None}
    n_cells = len(frames[0])
    needed_cells = int(area_fraction * n_cells) + 1  # strictly more than area_fraction of the screen
    if needed_cells > n_cells:
        needed_cells = n_cells
    change_frames = _cell_change_frames(frames)
    events = sorted((times[fi], cell) for cell, fis in enumerate(change_frames) for fi in fis)

    duration_end = times[-1] if times else 0.0
    n_seconds = int(duration_end) + 1
    second_windows = [(float(k), float(k + 1)) for k in range(n_seconds)]
    per_second = []
    for (start, end), (changes, cells) in zip(second_windows, _window_counts(events, n_cells, needed_cells, second_windows)):
        per_second.append({"start": start, "end": end, "flashes": changes / 2.0, "changes": changes})

    sliding = [(t, t + 1.0) for t in times]
    worst = None
    for (start, end), (changes, cells) in zip(sliding, _window_counts(events, n_cells, needed_cells, sliding)):
        if worst is None or changes > worst["changes"]:
            worst = {
                "start": round(start, 6), "end": round(end, 6), "flashes": changes / 2.0, "changes": changes,
                "area_fraction": round(cells / n_cells, 4) if changes else 0.0,
            }
    return {"per_second": per_second, "worst_window": worst}


def _parse_frame_times(stderr: str) -> List[float]:
    times: Dict[int, float] = {}
    for m in _SHOWINFO_RE.finditer(stderr):
        times[int(m.group(1))] = float(m.group(2))
    return [times[k] for k in sorted(times)]


def parse_scene_cuts(stderr: str, frame_rate: Optional[float]) -> List[Dict[str, Any]]:
    """``[{time, frame, score}]`` from ``scdet`` + ``metadata=print``
    log lines. ``frame`` is the decoded frame index of the first frame of
    the new shot; ``score`` is scdet's own score (null if its log line was
    not seen)."""

    scores: Dict[str, float] = {}
    for m in _SCDET_SCORE_RE.finditer(stderr):
        scores[m.group(2)] = float(m.group(1))
    cuts: List[Dict[str, Any]] = []
    pending: Optional[Tuple[int, float]] = None
    for line in stderr.splitlines():
        m = _META_FRAME_RE.search(line)
        if m:
            pending = (int(m.group(1)), float(m.group(2)))
            continue
        m = _META_SCD_TIME_RE.search(line)
        if m and pending is not None:
            frame_index, pts_time = pending
            cuts.append({"time": pts_time, "frame": frame_index, "score": scores.get(m.group(1))})
            pending = None
    return cuts


def analyze_temporal(
    ffmpeg_path: str,
    input_path: Path,
    video_stream_index: int,
    *,
    frame_rate: Optional[float],
    color_transfer: Optional[str],
    want_flashes: bool,
    want_cuts: bool,
    flash_area_fraction: float = 0.25,
    scene_cut_threshold: float = 10.0,
    timeout: float = 120.0,
) -> TemporalResult:
    measurements: List[QCMeasurement] = []
    flash_supported = want_flashes and (color_transfer or "") not in _HDR_TRANSFERS

    filters: List[str] = []
    if want_cuts:
        filters += [f"scdet=threshold={float(scene_cut_threshold)}", "metadata=mode=print:key=lavfi.scd.time"]
    if flash_supported:
        filters += ["format=yuv444p", "extractplanes=y", f"scale={FLASH_GRID}:{FLASH_GRID}:flags=area", "showinfo"]

    result = None
    if filters:
        argv = ffmpeg_rawvideo_argv(
            ffmpeg_path, input_path, filters,
            stream_select=f"0:{video_stream_index}",
            pix_fmt="gray" if flash_supported else None,
        )
        try:
            result = run_argv(argv, timeout=timeout, binary_stdout=True)
        except FileNotFoundError:
            raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)
        if result.timed_out:
            raise error("TOOL_ERROR", "ffmpeg temporal analysis timed out", argv=argv, timeout=timeout)

    if want_flashes:
        if not flash_supported:
            note = f"not performed: HDR transfer {color_transfer!r} has no implemented BT.1702 display mapping"
            measurements.append(QCMeasurement("video.flash_per_second", "video", "flash_per_second", None, source="ffmpeg:extractplanes", notes=note))
            measurements.append(QCMeasurement("video.flash_worst_window", "video", "flash_worst_window", None, source="ffmpeg:extractplanes", notes=note))
        else:
            cell_bytes = FLASH_GRID * FLASH_GRID
            raw = result.stdout_bytes if result is not None else b""
            frames = [raw[i:i + cell_bytes] for i in range(0, len(raw) - cell_bytes + 1, cell_bytes)]
            times = _parse_frame_times(result.stderr if result is not None else "")
            note = FLASH_ASSUMPTION_NOTE
            if len(times) != len(frames):
                if frame_rate:
                    times = [i / frame_rate for i in range(len(frames))]
                    note += "; frame times derived from index/frame_rate (showinfo timestamps unavailable)"
                else:
                    frames, times = [], []
                    note += "; not performed: frame times unavailable"
            if frames:
                analysis = analyze_flashes(frames, times, flash_area_fraction)
                per_second, worst = analysis["per_second"], analysis["worst_window"]
            else:
                per_second, worst = None, None
            measurements.append(QCMeasurement("video.flash_per_second", "video", "flash_per_second", per_second, unit="flashes", source="ffmpeg:extractplanes", notes=note))
            measurements.append(QCMeasurement("video.flash_worst_window", "video", "flash_worst_window", worst, unit="flashes", source="ffmpeg:extractplanes", notes=note))
        measurements.append(QCMeasurement("video.red_flash_count", "video", "red_flash_count", None, source="not_measured", notes=RED_FLASH_NOTE))

    if want_cuts:
        cuts = parse_scene_cuts(result.stderr if result is not None else "", frame_rate)
        measurements.append(
            QCMeasurement(
                "video.scene_cuts", "video", "scene_cuts", cuts, source="ffmpeg:scdet",
                notes=f"scdet threshold {float(scene_cut_threshold)}; time/frame of the first frame of each new shot",
            )
        )
    return TemporalResult(measurements=measurements)
