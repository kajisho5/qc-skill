"""Alpha-edge measurement (ADR-027) - measured facts only.

Title and stinger material carries alpha in a declared mode: ``straight``
(the colour is the colour, alpha says how much of it) or
``premultiplied`` (the colour is already multiplied by alpha). Read in the
wrong mode the edges composite with a halo: dark for a premultiplied file
read as straight, light for a straight file read as premultiplied.
``video.alpha_edges`` reads sampled frames as 8-bit RGBA and reports, per
frame:

  * ``mode`` - the pixels whose colour is above their alpha (any of R, G,
    B more than 2 of 255 over A). A premultiplied file cannot have one.
  * ``fringe`` - the partly transparent pixels (0 < alpha < 255) next to
    the graphic composited over black and over white at the declared
    mode, against the same pixel composited from the graphic's own colour
    at its alpha. The graphic's colour is taken from the nearest opaque
    pixels: a partly transparent pixel touching an opaque one (8
    neighbours) takes their mean colour, the next ring inward from it,
    up to 8 pixels deep; a partly transparent pixel further from any
    opaque one (a soft shadow or glow) is not judged. A pixel whose
    composite is off by more than 16 of 255 in any channel over either
    background is a fringe pixel (dark below, light above); the fringe's
    width is, for each fringe pixel, the shorter of the horizontal and
    vertical runs of fringe pixels through it, and the frame's width is
    the largest of those.

Pure Python + ffmpeg/ffprobe, no numpy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import QCMeasurement
from .frames import count_frames
from .stinger import _stream_origin, alpha_pixel_formats, frame_at, grab_frame, stream_frame_rate

MODE_TOLERANCE = 2  # of 255: a colour this far above its alpha still counts as premultiplied (rounding)
FRINGE_TOLERANCE = 16  # of 255: a composite further than this from the graphic's own composite is a fringe pixel
MAX_EDGE_DEPTH = 8  # pixels: how far into a soft edge the graphic's colour is carried from the opaque pixels
DEFAULT_SAMPLE_FRAMES = 5
BACKGROUNDS = (("black", 0), ("white", 255))

NOTE = (
    "sampled frames as 8-bit RGBA; mode: pixels with a colour more than 2/255 above their alpha; fringe: partly "
    "transparent pixels up to 8 px from the opaque graphic, composited over black and white at the declared mode and "
    "compared with the graphic's own colour at that alpha (more than 16/255 off is a fringe pixel)"
)

_NEIGHBOURS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def composite(c: float, a: int, bg: int, mode: str) -> float:
    """One channel of a pixel over a background value, at ``mode``,
    clipped to 0-255."""

    if mode == "premultiplied":
        out = c + bg * (255 - a) / 255
    else:
        out = c * a / 255 + bg * (255 - a) / 255
    return min(255.0, max(0.0, out))


def mode_facts(raw: bytes) -> Dict[str, Any]:
    """Pixels whose colour is above their alpha (RGBA bytes)."""

    over = 0
    worst: Optional[Tuple[int, int, int]] = None  # (excess, pixel index, alpha)
    for i in range(0, len(raw), 4):
        a = raw[i + 3]
        m = max(raw[i], raw[i + 1], raw[i + 2])
        if m > a + MODE_TOLERANCE:
            over += 1
            if worst is None or m - a > worst[0]:
                worst = (m - a, i // 4, a)
    return {"over_alpha_pixels": over, "worst": worst}


def _edge_colours(raw: bytes, width: int, height: int, partial: List[int]) -> Dict[int, Tuple[float, float, float]]:
    """For partly transparent pixels, the graphic's colour carried in from
    the nearest opaque pixels, ring by ring (at most MAX_EDGE_DEPTH)."""

    alpha = raw[3::4]
    pending = set(partial)
    colours: Dict[int, Tuple[float, float, float]] = {}

    def neighbours(i: int):
        x, y = i % width, i // width
        for dx, dy in _NEIGHBOURS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                yield ny * width + nx

    ring: Dict[int, Tuple[float, float, float]] = {}
    for i in pending:
        src = [j for j in neighbours(i) if alpha[j] == 255]
        if src:
            n = len(src)
            ring[i] = (
                sum(raw[4 * j] for j in src) / n, sum(raw[4 * j + 1] for j in src) / n, sum(raw[4 * j + 2] for j in src) / n,
            )
    depth = 1
    while ring and depth <= MAX_EDGE_DEPTH:
        colours.update(ring)
        pending.difference_update(ring)
        if depth == MAX_EDGE_DEPTH:
            break
        nxt: Dict[int, Tuple[float, float, float]] = {}
        for i in pending:
            src = [colours[j] for j in neighbours(i) if j in ring]
            if src:
                n = len(src)
                nxt[i] = (sum(c[0] for c in src) / n, sum(c[1] for c in src) / n, sum(c[2] for c in src) / n)
        ring = nxt
        depth += 1
    return colours


def _run_lengths(pixels: List[int], width: int) -> Dict[int, int]:
    """For each pixel index, the length of the run of listed pixels
    through it along a row (indices sorted by row then column)."""

    out: Dict[int, int] = {}
    run: List[int] = []
    for i in pixels:
        if run and (i != run[-1] + 1 or i // width != run[-1] // width):
            for j in run:
                out[j] = len(run)
            run = []
        run.append(i)
    for j in run:
        out[j] = len(run)
    return out


def fringe_width(pixels: List[int], width: int, height: int) -> int:
    """The largest, over the listed pixels, of the shorter of the
    horizontal and vertical runs of listed pixels through it."""

    if not pixels:
        return 0
    rows = _run_lengths(sorted(pixels), width)
    transposed = sorted((i % width) * height + i // width for i in pixels)
    cols_t = _run_lengths(transposed, height)
    cols = {(t % height) * width + t // height: n for t, n in cols_t.items()}
    return max(min(rows[i], cols[i]) for i in pixels)


def fringe_facts(raw: bytes, width: int, height: int, mode: str) -> Dict[str, Any]:
    alpha = raw[3::4]
    partial = [i for i, a in enumerate(alpha) if 0 < a < 255]
    colours = _edge_colours(raw, width, height, partial) if partial else {}
    fringe: List[int] = []
    dark = light = 0
    worst = {name: 0.0 for name, _ in BACKGROUNDS}
    first: Optional[Dict[str, Any]] = None
    for i in sorted(colours):
        a = alpha[i]
        ref = colours[i]
        deviation = 0.0
        for name, bg in BACKGROUNDS:
            for ch in range(3):
                got = composite(raw[4 * i + ch], a, bg, mode)
                want = composite(ref[ch] * a / 255 if mode == "premultiplied" else ref[ch], a, bg, mode)
                d = got - want
                if abs(d) > abs(deviation):
                    deviation = d
                worst[name] = max(worst[name], abs(d))
        if abs(deviation) > FRINGE_TOLERANCE:
            fringe.append(i)
            if deviation < 0:
                dark += 1
            else:
                light += 1
            if first is None:
                first = {"x": i % width, "y": i // width, "alpha": a, "deviation": round(deviation, 3)}
    return {
        "partial_pixels": len(partial), "judged_pixels": len(colours), "unjudged_pixels": len(partial) - len(colours),
        "fringe_pixels": len(fringe), "dark_pixels": dark, "light_pixels": light,
        "width_px": fringe_width(fringe, width, height),
        "max_deviation": {k: round(v, 3) for k, v in worst.items()}, "first": first,
    }


def _sample_frames(ffprobe_path: str, path: Path, stream: Dict[str, Any], rule: Any, rate) -> Tuple[Optional[List[int]], Optional[str]]:
    if rule.times:
        return sorted({frame_at(t, rate)[0] for t in rule.times}), None
    frames, reason = count_frames(ffprobe_path, path, int(stream["index"]))
    if frames is None:
        return None, reason
    if frames <= 0:
        return None, "the stream has no frames"
    n = min(DEFAULT_SAMPLE_FRAMES, frames)
    if n == 1:
        return [0], None
    return sorted({round(k * (frames - 1) / (n - 1)) for k in range(n)}), None


def _measure(
    ffmpeg_path: str, ffprobe_path: str, path: Path, probe_data: Dict[str, Any], stream: Dict[str, Any], rule: Any,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"measured": False, "alpha_mode": rule.alpha_mode, "pix_fmt": stream.get("pix_fmt")}
    formats = alpha_pixel_formats(ffprobe_path)
    if formats is None:
        entry["reason"] = "ffprobe could not list which pixel formats carry alpha"
        return entry
    if stream.get("pix_fmt") not in formats:
        entry["reason"] = f"the stream's pixel format {stream.get('pix_fmt')!r} has no alpha channel"
        return entry
    rate = stream_frame_rate(stream)
    if rate is None:
        entry["reason"] = "the stream's frame rate could not be measured"
        return entry
    entry["frame_rate"] = f"{rate.numerator}/{rate.denominator}"
    indices, reason = _sample_frames(ffprobe_path, path, stream, rule, rate)
    if indices is None:
        entry["reason"] = reason
        return entry
    origin = _stream_origin(probe_data, stream)
    frames: List[Dict[str, Any]] = []
    for n in indices:
        got = grab_frame(ffmpeg_path, path, int(stream["index"]), n, rate, ["format=rgba"], pix_fmt="rgba", origin=origin)
        item: Dict[str, Any] = {"frame": n, "measured": False}
        if "reason" in got:
            item["reason"] = got["reason"]
            frames.append(item)
            continue
        width, height = got["width"], got["height"]
        raw = got["raw"][: width * height * 4]
        if len(raw) < width * height * 4:
            item["reason"] = f"frame {n} is shorter than {width}x{height} RGBA"
            frames.append(item)
            continue
        mode = mode_facts(raw)
        worst = mode.pop("worst")
        if worst is not None:
            mode["worst"] = {"x": worst[1] % width, "y": worst[1] // width, "alpha": worst[2], "excess": worst[0]}
        item.update(
            measured=True, time=got["time"], width=width, height=height, pixels=width * height,
            mode=mode, fringe=fringe_facts(raw, width, height, rule.alpha_mode),
        )
        frames.append(item)
    entry.update(measured=True, frames=frames)
    return entry


def analyze_alpha_edge(
    ffmpeg_path: str, ffprobe_path: str, input_path: Path, probe_data: Dict[str, Any], stream: Dict[str, Any], rule: Any,
) -> List[QCMeasurement]:
    value = _measure(ffmpeg_path, ffprobe_path, input_path, probe_data, stream, rule)
    return [QCMeasurement("video.alpha_edges", "video", "alpha_edges", value, source="ffmpeg:rawvideo", notes=NOTE)]


def not_performed(rule: Any, reason: str) -> List[QCMeasurement]:
    value = {"measured": False, "alpha_mode": rule.alpha_mode, "reason": reason}
    return [QCMeasurement("video.alpha_edges", "video", "alpha_edges", value, source="ffmpeg:rawvideo", notes=NOTE)]
