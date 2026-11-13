"""Key / fill pair measurements (ADR-028) - measured facts only.

A broadcast switcher keys a graphic from two files played together: the
fill (the colour) and the key (the alpha as a luma matte: video black is
transparent, video white opaque). The input is the fill; the key is
``VideoRule.key_fill.key_file``.

  * ``video.key_fill_pair`` - both streams' size, exact frame rate,
    frame count (packets, counted without decoding), start time and
    timecode tag, and which of them differ. Nothing is decoded until
    these match.
  * ``video.key_fill_frames`` - every frame of both, decoded in chunks
    of at most 32 MiB. The key's luma is read as 0-255 from its range
    (``key_range``, else the stream's ``color_range``; unknown is read as
    limited, video black 16 to white 235 at 8 bit). Where the fill
    carries alpha, a pixel is off when the key differs from the fill's
    alpha by more than ``tolerance``; where it does not, a pixel is off
    when the key is at most ``tolerance`` (outside the key) and the
    fill's luma, read as 0-255 from the fill's own range the same way, is
    above ``tolerance`` (the fill is lit there). Luma, not RGB: a 4:2:2
    fill's chroma reaches a pixel past a vertical edge by construction.
    A frame differs when its share of pixels off is above
    ``max_off_fraction``.

Pure Python + ffmpeg/ffprobe, no numpy.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from ..errors import QCError
from ..models import QCMeasurement
from ..probe import probe_media, video_streams
from .frames import count_frames, grab_frames
from .stinger import _stream_origin, alpha_pixel_formats, stream_frame_rate

CHUNK_BYTES = 32 * 1024 * 1024
DEFAULT_TOLERANCE = 2  # of 255, the tolerance ffmpeg-skill's key_level step uses
MAX_LISTED_FRAMES = 10

PAIR_NOTE = "both streams' width, height, r_frame_rate, packet count, start_time and timecode tag"
FRAMES_NOTE = (
    "every frame: key luma (0-255 by its range) against the fill's alpha, or - for a fill without alpha - the fill's "
    "luma (0-255 by its range) where the key is at most the tolerance"
)


def _timecode(probe_data: Dict[str, Any], stream: Dict[str, Any]) -> Optional[str]:
    for tags in ((stream.get("tags") or {}), (probe_data.get("format", {}).get("tags") or {})):
        tc = tags.get("timecode")
        if isinstance(tc, str) and tc:
            return tc
    return None


def _facts(ffprobe_path: str, path: Path, probe_data: Dict[str, Any], stream: Dict[str, Any]) -> Dict[str, Any]:
    rate = stream_frame_rate(stream)
    frames, _ = count_frames(ffprobe_path, path, int(stream["index"]))
    formats = alpha_pixel_formats(ffprobe_path) or set()
    try:
        start = round(float(stream.get("start_time")), 6)
    except (TypeError, ValueError):
        start = None
    return {
        "width": stream.get("width"), "height": stream.get("height"),
        "frame_rate": None if rate is None else f"{rate.numerator}/{rate.denominator}",
        "frames": frames, "start_time": start, "timecode": _timecode(probe_data, stream),
        "pix_fmt": stream.get("pix_fmt"), "alpha": stream.get("pix_fmt") in formats,
        "color_range": stream.get("color_range"),
    }


def _stream_range(facts: Dict[str, Any]) -> str:
    return "full" if facts.get("color_range") in ("pc", "jpeg") else "limited"


def _key_range(rule: Any, key_facts: Dict[str, Any]) -> str:
    return rule.key_range if rule.key_range is not None else _stream_range(key_facts)


def _luma_filters(range_name: str) -> List[str]:
    return [f"scale=in_range={'pc' if range_name == 'full' else 'tv'}:out_range=pc", "format=gray"]


def _mismatches(fill: Dict[str, Any], key: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name in ("width", "height", "frame_rate", "frames", "timecode"):
        if fill[name] != key[name]:
            out.append({"field": name, "fill": fill[name], "key": key[name]})
    fs, ks = fill["start_time"], key["start_time"]
    rate = fill["frame_rate"]
    if fs is None or ks is None:
        if fs != ks:
            out.append({"field": "start_time", "fill": fs, "key": ks})
    elif rate is not None:
        num, _, den = rate.partition("/")
        if abs(fs - ks) * int(num) / int(den) >= 0.5:  # half a frame or more apart
            out.append({"field": "start_time", "fill": fs, "key": ks})
    return out


def _off_pixels(key: bytes, fill: bytes, mode: str, tolerance: int) -> int:
    if mode == "alpha":
        if key == fill:
            return 0
        return sum(1 for k, a in zip(key, fill) if abs(k - a) > tolerance)
    return sum(1 for k, y in zip(key, fill) if k <= tolerance and y > tolerance)


def _compare(
    ffmpeg_path: str, fill_path: Path, fill_probe: Dict[str, Any], fill_stream: Dict[str, Any],
    key_path: Path, key_probe: Dict[str, Any], key_stream: Dict[str, Any],
    facts: Dict[str, Any], rule: Any, key_range: str,
) -> Dict[str, Any]:
    fill_facts = facts["fill"]
    width, height, frames = fill_facts["width"], fill_facts["height"], fill_facts["frames"]
    rate = stream_frame_rate(fill_stream)
    mode = "alpha" if fill_facts["alpha"] else "black_outside_key"
    tolerance = rule.tolerance if rule.tolerance is not None else DEFAULT_TOLERANCE
    limit = rule.max_off_fraction if rule.max_off_fraction is not None else 0.0
    entry: Dict[str, Any] = {
        "measured": False, "mode": mode, "key_range": key_range, "tolerance": tolerance, "max_off_fraction": limit,
    }
    pixels = width * height
    if frames <= 0 or pixels <= 0:
        entry["reason"] = "the pair has no frames to compare"
        return entry
    if mode == "alpha":
        fill_filters = ["alphaextract", "format=gray"]
    else:
        entry["fill_range"] = _stream_range(fill_facts)
        fill_filters = _luma_filters(entry["fill_range"])
    key_filters = _luma_filters(key_range)
    per_chunk = max(1, min(frames, CHUNK_BYTES // pixels))
    fill_origin, key_origin = _stream_origin(fill_probe, fill_stream), _stream_origin(key_probe, key_stream)
    differing: List[Dict[str, Any]] = []
    differing_count = 0
    worst: Optional[Dict[str, Any]] = None
    compared = 0
    start = 0
    while start < frames:
        n = min(per_chunk, frames - start)
        got_fill = grab_frames(
            ffmpeg_path, fill_path, int(fill_stream["index"]), start, n, rate, fill_filters,
            pix_fmt="gray", frame_bytes=pixels, origin=fill_origin,
        )
        got_key = grab_frames(
            ffmpeg_path, key_path, int(key_stream["index"]), start, n, rate, key_filters,
            pix_fmt="gray", frame_bytes=pixels, origin=key_origin,
        )
        for name, got in (("fill", got_fill), ("key", got_key)):
            if "reason" in got:
                entry["reason"] = f"the {name}: {got['reason']}"
                return entry
            if len(got["frames"]) < n:
                entry["reason"] = f"the {name} ended at frame {got['frames'][-1]['index']}, before its counted {frames} frames"
                return entry
        for f, k in zip(got_fill["frames"], got_key["frames"]):
            off = _off_pixels(k["raw"], f["raw"], mode, tolerance)
            share = round(off / pixels, 6)
            compared += 1
            if worst is None or share > worst["off_fraction"]:
                worst = {"frame": f["index"], "time": f["time"], "off_pixels": off, "off_fraction": share}
            if share > limit:
                differing_count += 1
                if len(differing) < MAX_LISTED_FRAMES:
                    differing.append({"frame": f["index"], "time": f["time"], "off_pixels": off, "off_fraction": share})
        start += n
    entry.update(
        measured=True, frames_compared=compared, pixels_per_frame=pixels, differing_frames=differing_count,
        first_differing=differing[0] if differing else None, listed_differing=differing, worst=worst,
    )
    return entry


def _key_stream(ffprobe_path: str, key_path: Path):
    try:
        probe_data = probe_media(ffprobe_path, key_path)
    except QCError as exc:
        if exc.code != "UNSUPPORTED_FORMAT":
            raise
        return None, None, f"the key file could not be read: {exc.message}"
    streams = video_streams(probe_data)
    if not streams:
        return None, None, "the key file has no video stream"
    return probe_data, streams[0], None


def analyze_key_fill(
    ffmpeg_path: str, ffprobe_path: str, fill_path: Path, fill_probe: Dict[str, Any], fill_stream: Dict[str, Any],
    rule: Any, key_path: Path,
) -> List[QCMeasurement]:
    key_probe, key_stream, failure = _key_stream(ffprobe_path, key_path)
    if failure is not None:
        pair: Dict[str, Any] = {"measured": False, "reason": failure}
        frames: Dict[str, Any] = {"measured": False, "reason": failure}
    else:
        facts = {"fill": _facts(ffprobe_path, fill_path, fill_probe, fill_stream), "key": _facts(ffprobe_path, key_path, key_probe, key_stream)}
        unread = [
            f"the {side}'s {name}" for side in ("fill", "key") for name in ("width", "height", "frame_rate", "frames")
            if facts[side][name] is None
        ]
        if unread:
            reason = f"{', '.join(unread)} could not be measured"
            pair = {"measured": False, "reason": reason, **facts}
            frames = {"measured": False, "reason": reason}
        else:
            mismatches = _mismatches(facts["fill"], facts["key"])
            pair = {"measured": True, **facts, "mismatches": mismatches}
            key_range = _key_range(rule, facts["key"])
            if mismatches:
                frames = {
                    "measured": False, "key_range": key_range,
                    "reason": "not compared: the pair differs in " + ", ".join(m["field"] for m in mismatches),
                }
            else:
                frames = _compare(
                    ffmpeg_path, fill_path, fill_probe, fill_stream, key_path, key_probe, key_stream, facts, rule, key_range,
                )
    return [
        QCMeasurement("video.key_fill_pair", "video", "key_fill_pair", pair, source="ffprobe", notes=PAIR_NOTE),
        QCMeasurement("video.key_fill_frames", "video", "key_fill_frames", frames, source="ffmpeg:rawvideo", notes=FRAMES_NOTE),
    ]


def not_performed(reason: str) -> List[QCMeasurement]:
    return [
        QCMeasurement("video.key_fill_pair", "video", "key_fill_pair", {"measured": False, "reason": reason}, source="ffprobe", notes=PAIR_NOTE),
        QCMeasurement(
            "video.key_fill_frames", "video", "key_fill_frames", {"measured": False, "reason": reason},
            source="ffmpeg:rawvideo", notes=FRAMES_NOTE,
        ),
    ]
