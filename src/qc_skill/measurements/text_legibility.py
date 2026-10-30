"""Text legibility at caller-named regions (ADR-018) - measured facts
only: contrast ratio and ink line height. Never OCR, never a judgement
of what the text says or whether it "reads well".

For each ``TextRegion {t, box: [x, y, w, h], role}`` one frame is
decoded at ``t`` (ffmpeg input seek), converted to RGB24 and cropped to
the box. Then, in pure Python:

  * every pixel gets a gamma-encoded luma (0.2126 R' + 0.7152 G' +
    0.0722 B') for Otsu thresholding, and a WCAG 2.x relative luminance
    (sRGB-linearised, same weights) for the contrast measurement;
  * Otsu's threshold splits the box into two classes; the minority class
    is the ink, the other the background;
  * contrast ratio = (L1 + 0.05) / (L2 + 0.05) on the two classes'
    median relative luminance (WCAG 2.x definition, L1 the lighter);
  * ink line height = longest run of consecutive pixel rows containing
    any ink pixel. For all-caps text without descenders this is the cap
    height; for mixed-case text it includes ascenders/descenders, so it
    is an upper bound on cap height. It is also reported scaled to a
    1080 px short side (``height * 1080 / min(width, height)``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..errors import error
from ..models import QCMeasurement
from ..runner import ffmpeg_rawvideo_argv, run_argv


def _srgb_to_linear(c8: int) -> float:
    c = c8 / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


_LIN = [_srgb_to_linear(i) for i in range(256)]

TEXT_MEASUREMENT_NOTE = (
    "per region: Otsu split on gamma-encoded luma; contrast = WCAG 2.x (L1+0.05)/(L2+0.05) on the two classes' "
    "median sRGB-linearised relative luminance; text_height_px = tallest run of rows containing ink (minority "
    "class) - cap height for caps-only text, an upper bound on it otherwise; *_at_1080 scales to a 1080 px short side"
)


@dataclass
class TextResult:
    measurements: List[QCMeasurement] = field(default_factory=list)


def _otsu_threshold(hist: Sequence[int]) -> Optional[int]:
    """Classic Otsu over a 256-bin histogram; returns t such that class 0
    is values <= t. ``None`` when the region has a single value."""

    total = sum(hist)
    if total == 0:
        return None
    sum_all = sum(i * h for i, h in enumerate(hist))
    w0 = 0
    sum0 = 0.0
    best_t, best_var = None, -1.0
    for t in range(255):
        w0 += hist[t]
        if w0 == 0:
            continue
        w1 = total - w0
        if w1 == 0:
            break
        sum0 += t * hist[t]
        m0 = sum0 / w0
        m1 = (sum_all - sum0) / w1
        var = w0 * w1 * (m0 - m1) ** 2
        if var > best_var:  # strict: the lowest t wins ties - deterministic
            best_var, best_t = var, t
    return best_t


def _median(values: List[float]) -> float:
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def measure_text_pixels(rgb: bytes, width: int, height: int) -> Dict[str, Any]:
    """Pure function over one cropped RGB24 box. Exposed for unit tests."""

    n = width * height
    luma: List[int] = [0] * n
    rel: List[float] = [0.0] * n
    hist = [0] * 256
    lin = _LIN
    for i in range(n):
        r, g, b = rgb[3 * i], rgb[3 * i + 1], rgb[3 * i + 2]
        y8 = int(0.2126 * r + 0.7152 * g + 0.0722 * b + 0.5)
        luma[i] = y8
        hist[y8] += 1
        rel[i] = 0.2126 * lin[r] + 0.7152 * lin[g] + 0.0722 * lin[b]

    t = _otsu_threshold(hist)
    if t is None:
        lum = _median(rel)
        return {
            "contrast_ratio": 1.0, "ink_luminance": None, "background_luminance": round(lum, 6),
            "ink_fraction": 0.0, "text_height_px": 0, "line_heights_px": [],
        }

    dark = [i for i in range(n) if luma[i] <= t]
    light_count = n - len(dark)
    ink_is_dark = len(dark) <= light_count
    dark_set_flags = [False] * n
    for i in dark:
        dark_set_flags[i] = True
    ink_flags = dark_set_flags if ink_is_dark else [not f for f in dark_set_flags]

    ink_lum = _median([rel[i] for i in range(n) if ink_flags[i]])
    bg_lum = _median([rel[i] for i in range(n) if not ink_flags[i]])
    l1, l2 = max(ink_lum, bg_lum), min(ink_lum, bg_lum)
    contrast = (l1 + 0.05) / (l2 + 0.05)

    runs: List[int] = []
    run = 0
    for row in range(height):
        base = row * width
        if any(ink_flags[base:base + width]):
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)

    return {
        "contrast_ratio": round(contrast, 3),
        "ink_luminance": round(ink_lum, 6),
        "background_luminance": round(bg_lum, 6),
        "ink_fraction": round(sum(ink_flags) / n, 4),
        "text_height_px": max(runs, default=0),
        "line_heights_px": runs,
    }


def analyze_text_regions(
    ffmpeg_path: str,
    input_path: Path,
    video_stream_index: int,
    regions: Sequence[Any],
    *,
    frame_width: Optional[int],
    frame_height: Optional[int],
    timeout: float = 60.0,
) -> TextResult:
    results: List[Dict[str, Any]] = []
    short_side = min(frame_width, frame_height) if frame_width and frame_height else None
    for index, region in enumerate(regions):
        x, y, w, h = (int(v) for v in region.box)
        entry: Dict[str, Any] = {"index": index, "t": region.t, "box": [x, y, w, h], "role": region.role}
        if frame_width is None or frame_height is None:
            entry.update(measured=False, reason="frame size could not be measured")
            results.append(entry)
            continue
        if x + w > frame_width or y + h > frame_height:
            entry.update(measured=False, reason=f"box extends outside the {frame_width}x{frame_height} frame")
            results.append(entry)
            continue
        argv = ffmpeg_rawvideo_argv(
            ffmpeg_path, input_path, ["format=rgb24", f"crop={w}:{h}:{x}:{y}"],
            stream_select=f"0:{video_stream_index}", pix_fmt="rgb24",
            seek_sec=float(region.t), max_frames=1, log_level="error",
        )
        try:
            res = run_argv(argv, timeout=timeout, binary_stdout=True)
        except FileNotFoundError:
            raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)
        if res.timed_out:
            raise error("TOOL_ERROR", "ffmpeg text-region frame grab timed out", argv=argv, timeout=timeout)
        raw = res.stdout_bytes
        if len(raw) < w * h * 3:
            entry.update(measured=False, reason=f"no decodable frame at t={region.t}s")
            results.append(entry)
            continue
        facts = measure_text_pixels(raw[: w * h * 3], w, h)
        entry.update(measured=True, **facts)
        entry["text_height_px_at_1080"] = round(facts["text_height_px"] * 1080.0 / short_side, 2) if short_side else None
        results.append(entry)

    return TextResult(
        measurements=[
            QCMeasurement("video.text_regions", "video", "text_regions", results, source="ffmpeg:crop", notes=TEXT_MEASUREMENT_NOTE)
        ]
    )
