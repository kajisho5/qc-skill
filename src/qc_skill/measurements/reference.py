"""Opt-in full-reference metrics: VMAF, SSIM, PSNR against
``request.reference_video`` (ADR-021).

Run only when a ``VideoRule`` names ``reference_metrics``; a request that
does not still decodes its input exactly once (ADR-006). One extra ffmpeg
invocation decodes both files and feeds the aligned pair to the
``libvmaf``, ``ssim`` and ``psnr`` filters at once.

Alignment rule (ADR-021), stated here rather than hidden in the graph:

  * Both streams are re-timed to start at 0 (``setpts=PTS-STARTPTS``) and
    compared frame by frame up to the shorter one (``shortest=1``).
  * **Frame rate must match** (within 0.01 fps) and the frame counts may
    differ by at most one frame. Otherwise the comparison is *refused*:
    ``video.reference_alignment`` is ``{status: "refused", code, reason,
    distorted, reference}`` and every requested score is ``null`` with
    that reason, so each check is UNKNOWN. Pairing frame N of one clip
    with frame N of a clip at another rate, or of a clip that was trimmed,
    measures the misalignment, not the encode - qc-skill does not retime
    or guess an offset.
  * **Size may differ**: the distorted picture is scaled to the
    reference's size (bicubic) and the metrics are computed there - the
    VMAF FAQ's rule ("upsample the 480 video to 1080, and calculate the
    VMAF at 1080, together with its 1080 source"). Recorded as
    ``scaled_distorted_to``.
  * Both sides are converted to one pixel format: the reference's own when
    libvmaf accepts it (planar YUV 4:2:0/4:2:2/4:4:4, 8 or 10 bit),
    otherwise ``yuv420p``.

Values: ``video.vmaf`` is libvmaf's pooled mean with its default model;
``video.ssim`` is ffmpeg's ``All`` (Y/U/V weighted) mean SSIM;
``video.psnr_db`` is ffmpeg's ``average`` PSNR. A bit-identical pair has
infinite PSNR, which JSON (and the canonical identity hash) cannot carry:
it is reported as libvmaf's own PSNR ceiling for the bit depth (60 dB at
8 bit, 72 dB at 10 bit) with ``psnr_capped`` in the alignment record.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..errors import error
from ..models import QCMeasurement
from ..runner import ffmpeg_two_input_argv, run_argv

FRAME_RATE_TOLERANCE = 0.01  # fps; the same tolerance video.variable_frame_rate_suspected uses
MAX_FRAME_COUNT_DELTA = 1
LIBVMAF_PIX_FMTS = ("yuv420p", "yuv422p", "yuv444p", "yuv420p10le", "yuv422p10le", "yuv444p10le")
PSNR_CAP_DB = {8: 60.0, 10: 72.0}  # libvmaf psnr_max per bit depth (libvmaf/src/feature psnr_tools)

_VMAF_RE = re.compile(r"Parsed_libvmaf.*?VMAF score:\s*(-?[\d.]+)")
_SSIM_RE = re.compile(r"Parsed_ssim.*?SSIM Y:([\d.]+).*?All:([\d.]+)")
_PSNR_RE = re.compile(r"Parsed_psnr.*?PSNR y:(\S+).*?average:(\S+)\s+min:(\S+)\s+max:(\S+)")

VMAF_NOTE = (
    "libvmaf pooled mean, default model (vmaf_v0.6.1 in libvmaf 2.x); computed at the reference's resolution "
    "(VMAF FAQ: upsample the distorted video to the reference's size)"
)
SSIM_NOTE = "ffmpeg ssim 'All' (mean over frames, Y/U/V weighted 4:1:1 for 4:2:0)"
PSNR_NOTE = "ffmpeg psnr 'average' over frames (dB); infinite (bit-identical) is reported as the libvmaf cap for the bit depth"


def _bit_depth(pix_fmt: str) -> int:
    return 10 if pix_fmt.endswith("10le") else 8


def plan_alignment(distorted: Dict[str, Any], reference: Dict[str, Any]) -> Dict[str, Any]:
    """Pure: decide whether two streams can be compared frame by frame and
    how. Each side is ``{width, height, frame_rate, frame_count,
    duration_sec, pix_fmt}`` (any value may be ``None``)."""

    record: Dict[str, Any] = {"distorted": distorted, "reference": reference}

    def refuse(code: str, reason: str) -> Dict[str, Any]:
        return {"status": "refused", "code": code, "reason": reason, **record}

    if not reference.get("width") or not reference.get("height"):
        return refuse("REFERENCE_NO_VIDEO", "the reference has no measurable video stream")
    if not distorted.get("width") or not distorted.get("height"):
        return refuse("DISTORTED_NO_VIDEO", "the input has no measurable video stream")
    d_fps, r_fps = distorted.get("frame_rate"), reference.get("frame_rate")
    if not d_fps or not r_fps:
        return refuse("FRAME_RATE_UNKNOWN", "the frame rate of the input or the reference could not be measured")
    if abs(d_fps - r_fps) > FRAME_RATE_TOLERANCE:
        return refuse("FRAME_RATE_MISMATCH", f"frame rate {d_fps} fps does not match the reference's {r_fps} fps")
    d_n, r_n = distorted.get("frame_count"), reference.get("frame_count")
    if d_n is not None and r_n is not None:
        if abs(d_n - r_n) > MAX_FRAME_COUNT_DELTA:
            return refuse("FRAME_COUNT_MISMATCH", f"{d_n} frames does not match the reference's {r_n} frames (more than {MAX_FRAME_COUNT_DELTA} apart)")
    else:
        d_dur, r_dur = distorted.get("duration_sec"), reference.get("duration_sec")
        if d_dur is None or r_dur is None:
            return refuse("LENGTH_UNKNOWN", "neither frame counts nor durations of both files could be measured")
        if abs(d_dur - r_dur) * r_fps > MAX_FRAME_COUNT_DELTA:
            return refuse("DURATION_MISMATCH", f"duration {d_dur}s does not match the reference's {r_dur}s (more than {MAX_FRAME_COUNT_DELTA} frame apart)")

    ref_fmt = reference.get("pix_fmt")
    pix_fmt = ref_fmt if ref_fmt in LIBVMAF_PIX_FMTS else "yuv420p"
    scaled = (distorted["width"], distorted["height"]) != (reference["width"], reference["height"])
    return {
        "status": "aligned",
        "compared_size": [reference["width"], reference["height"]],
        "scaled_distorted_to": [reference["width"], reference["height"]] if scaled else None,
        "pix_fmt": pix_fmt,
        "frame_rate": r_fps,
        "frame_rule": "both re-timed to start at 0; frame-by-frame up to the shorter stream",
        **record,
    }


def build_filter_complex(
    alignment: Dict[str, Any], metrics: List[str], distorted_stream_index: int = 0, reference_stream_index: int = 0
) -> Tuple[str, List[str]]:
    """Internally built graph (no caller text: sizes are ffprobe integers,
    the pixel format is one of ``LIBVMAF_PIX_FMTS``)."""

    width, height = alignment["compared_size"]
    pix_fmt = alignment["pix_fmt"]
    n = len(metrics)
    scale = f"scale={int(width)}:{int(height)}:flags=bicubic," if alignment.get("scaled_distorted_to") else ""
    d_labels = "".join(f"[d{i}]" for i in range(n))
    r_labels = "".join(f"[r{i}]" for i in range(n))
    parts = [
        f"[0:{int(distorted_stream_index)}]setpts=PTS-STARTPTS,{scale}format={pix_fmt},split={n}{d_labels}",
        f"[1:{int(reference_stream_index)}]setpts=PTS-STARTPTS,format={pix_fmt},split={n}{r_labels}",
    ]
    outputs = []
    for i, metric in enumerate(metrics):
        name = {"vmaf": "libvmaf", "ssim": "ssim", "psnr": "psnr"}[metric]
        parts.append(f"[d{i}][r{i}]{name}=shortest=1[o{i}]")
        outputs.append(f"[o{i}]")
    return ";".join(parts), outputs


def _number(text: str) -> Optional[float]:
    try:
        return float(text)
    except ValueError:
        return None


def parse_metric_output(stderr: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    m = None
    for m in _VMAF_RE.finditer(stderr):
        pass
    if m is not None:
        out["vmaf"] = float(m.group(1))
    m = None
    for m in _SSIM_RE.finditer(stderr):
        pass
    if m is not None:
        out["ssim"] = float(m.group(2))
    m = None
    for m in _PSNR_RE.finditer(stderr):
        pass
    if m is not None:
        out["psnr"] = m.group(2)  # raw text: may be "inf"
    return out


def _measurements(values: Dict[str, Optional[float]], notes: Dict[str, str], alignment: Optional[Dict[str, Any]], alignment_note: Optional[str], metrics: List[str]) -> List[QCMeasurement]:
    out = [
        QCMeasurement(
            "video.reference_alignment", "video", "reference_alignment", alignment,
            source="ffprobe:derived", notes=alignment_note,
        )
    ]
    if "vmaf" in metrics:
        out.append(QCMeasurement("video.vmaf", "video", "vmaf", values.get("vmaf"), source="ffmpeg:libvmaf", notes=notes["vmaf"]))
    if "ssim" in metrics:
        out.append(QCMeasurement("video.ssim", "video", "ssim", values.get("ssim"), source="ffmpeg:ssim", notes=notes["ssim"]))
    if "psnr" in metrics:
        out.append(QCMeasurement("video.psnr_db", "video", "psnr_db", values.get("psnr"), unit="dB", source="ffmpeg:psnr", notes=notes["psnr"]))
    return out


def not_performed(metrics: List[str], reason: str, alignment: Optional[Dict[str, Any]] = None) -> List[QCMeasurement]:
    note = f"not performed: {reason}"
    return _measurements({}, {k: note for k in ("vmaf", "ssim", "psnr")}, alignment, note, metrics)


def analyze_reference_metrics(
    ffmpeg_path: str,
    distorted_path: Path,
    distorted_stream_index: int,
    reference_path: Path,
    reference_stream_index: int,
    *,
    distorted: Dict[str, Any],
    reference: Dict[str, Any],
    metrics: List[str],
    vmaf_available: bool,
    timeout: float = 600.0,
) -> List[QCMeasurement]:
    alignment = plan_alignment(distorted, reference)
    if alignment["status"] != "aligned":
        return not_performed(metrics, f"reference comparison refused ({alignment['code']}): {alignment['reason']}", alignment)

    notes = {"vmaf": VMAF_NOTE, "ssim": SSIM_NOTE, "psnr": PSNR_NOTE}
    run_metrics = [m for m in metrics if m != "vmaf" or vmaf_available]
    if "vmaf" in metrics and not vmaf_available:
        notes["vmaf"] = "not performed: this ffmpeg has no libvmaf filter (built without --enable-libvmaf); see qc doctor"

    values: Dict[str, Optional[float]] = {}
    attempts = [run_metrics] + ([[m for m in run_metrics if m != "vmaf"]] if "vmaf" in run_metrics else [])
    failure = None
    done = False
    for attempt in attempts:
        if not attempt:
            break
        graph, outputs = build_filter_complex(alignment, attempt, distorted_stream_index, reference_stream_index)
        argv = ffmpeg_two_input_argv(ffmpeg_path, distorted_path, reference_path, graph, outputs)
        try:
            result = run_argv(argv, timeout=timeout)
        except FileNotFoundError:
            raise error("DEPENDENCY_ERROR", "ffmpeg executable could not be run", executable=ffmpeg_path)
        if result.timed_out:
            raise error("TOOL_ERROR", "ffmpeg reference-metric analysis timed out", argv=argv, timeout=timeout)
        if result.returncode != 0:
            # A libvmaf that is listed but fails at run time (e.g. no model)
            # must not take SSIM/PSNR down with it: retry without it once.
            failure = (result.stderr.strip().splitlines() or ["no output"])[-1][:300]
            continue
        values = parse_metric_output(result.stderr)
        for metric in attempt:
            if metric not in values:
                notes[metric] = f"not performed: ffmpeg {metric} printed no summary"
        if failure is not None:
            notes["vmaf"] = f"not performed: libvmaf failed at run time ({failure})"
        done = True
        break
    if not done:
        for metric in run_metrics:
            notes[metric] = f"not performed: ffmpeg failed ({failure})"

    raw_psnr = values.get("psnr")
    if raw_psnr is not None:
        number = _number(str(raw_psnr))
        if number is None or number == float("inf"):
            cap = PSNR_CAP_DB[_bit_depth(alignment["pix_fmt"])]
            values["psnr"] = cap
            alignment = {**alignment, "psnr_capped": True}
        else:
            values["psnr"] = round(number, 6)
    if values.get("ssim") is not None:
        values["ssim"] = round(values["ssim"], 6)
    if values.get("vmaf") is not None:
        values["vmaf"] = round(values["vmaf"], 6)
    return _measurements(values, notes, alignment, None, metrics)
