"""Typed rules and pure rule evaluation (STEP 9/10 of the task spec).

A Rule is caller-supplied, typed expectation data - never a hard-coded
policy inside this package (STEP 5: "'YouTube is always -14 LUFS' must not
be hard-coded"; STEP 10 forbids eval()/exec()/arbitrary expressions/shell).
Every ``evaluate_*`` function below is a pure function: measurements in,
(QCCheck, QCFinding) out. It never mutates a measurement, and every branch
is an explicit, enumerable comparison - no dynamic code execution of any
kind.

Two kinds of checks are produced:

  * "baseline" checks - objective invariants that hold regardless of any
    caller policy (a file that fails to decode is broken no matter what
    project it is for). These always run.
  * "policy" checks - only run when the caller supplies the relevant Rule
    field; a rule field left as ``None`` means "no opinion", and the
    corresponding check is simply not produced (STEP 5/10: never guess a
    default for a judgment call).
"""

from __future__ import annotations

import math
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

from .measurements.stinger import OPAQUE_MIN_ALPHA
from .models import FindingSeverity, QCCheck, QCFinding, QCMeasurement, QCStatus

MeasurementMap = Dict[str, QCMeasurement]


def _val(measurements: MeasurementMap, measurement_id: str) -> Any:
    m = measurements.get(measurement_id)
    return m.value if m is not None else None


def _unknown_check(check_id: str, category: str, reason: str, measurement_ids: List[str]) -> QCCheck:
    return QCCheck(check_id=check_id, category=category, status=QCStatus.UNKNOWN, measurement_ids=measurement_ids, reason=reason)


def _equality_check(
    checks: List[QCCheck],
    findings: List[QCFinding],
    *,
    check_id: str,
    category: str,
    measurement_id: str,
    actual: Any,
    expected: Any,
    code: str,
    message: str,
) -> None:
    """One measurement compared to one expected value.

    An unmeasured ``actual`` (``None``) is UNKNOWN, never a silent FAIL or
    PASS - some ffprobe fields (e.g. ``channel_layout`` on plain PCM/WAV)
    are legitimately absent even for a fully decodable, valid stream, and
    that must not be reported as "does not match expected".
    """

    if actual is None:
        checks.append(_unknown_check(check_id, category, f"{measurement_id} could not be measured", [measurement_id]))
        return
    status = QCStatus.PASS if actual == expected else QCStatus.FAIL
    check = QCCheck(check_id, category, status, [measurement_id])
    if status == QCStatus.FAIL:
        f = QCFinding(
            code, FindingSeverity.FAIL, message,
            evidence={"actual": actual, "expected": expected}, measurement_ids=[measurement_id],
        )
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)


# ---------------------------------------------------------------------------
# Video
# ---------------------------------------------------------------------------


TEXT_ROLES = ("hook", "cta", "caption", "term")

# Full-reference metrics a VideoRule can opt into (ADR-021), in the
# canonical order they are evaluated and reported in.
REFERENCE_METRICS = ("vmaf", "ssim", "psnr")


@dataclass
class TextRegion:
    """One caller-named on-screen text region to measure (ADR-018):
    ``t`` seconds, ``box`` ``[x, y, w, h]`` in the video's own pixel
    coordinates, ``role`` one of ``TEXT_ROLES``. qc-skill never finds
    text itself - it only measures where it is told text is."""

    t: float
    box: List[int]
    role: str


@dataclass
class StingerCover:
    """The window, on the stinger's own timeline, in which its maker
    states the graphic covers the whole picture (stinger.json ``cover``):
    the frames at ``start <= t < end``."""

    start: float
    end: float


@dataclass
class StingerRule:
    """A stinger transition to check (ADR-024). ``cut_point`` and
    ``cover`` are the stinger maker's statement (stinger.json); qc-skill
    reads no sidecar itself. Without ``file`` the input *is* the stinger.
    With ``file`` (the stinger, resolved and fingerprinted like any input)
    the input is a delivered video: the alpha is read from ``file``, and
    each ``cut_at`` time of the input is compared with the stinger's frame
    at its cut point (``max_cut_frame_difference``, no default)."""

    cut_point: float
    cover: Optional[StingerCover] = None
    file: Optional[str] = None
    cut_at: Optional[List[float]] = None
    max_cut_frame_difference: Optional[float] = None


@dataclass
class VideoRule:
    expected_width: Optional[int] = None
    expected_height: Optional[int] = None
    min_width: Optional[int] = None
    min_height: Optional[int] = None
    expected_frame_rate: Optional[float] = None
    frame_rate_tolerance: float = 0.05
    expected_codec: Optional[str] = None
    expected_pixel_format: Optional[str] = None
    expected_aspect_ratio: Optional[str] = None
    expected_color_range: Optional[str] = None
    expected_color_space: Optional[str] = None
    expected_color_transfer: Optional[str] = None
    expected_color_primaries: Optional[str] = None
    max_duration_sec: Optional[float] = None
    disallow_vfr: Optional[bool] = None
    max_single_black_sec: Optional[float] = None
    max_total_black_sec: Optional[float] = None
    max_single_freeze_sec: Optional[float] = None
    max_total_freeze_sec: Optional[float] = None
    max_single_luminance_excursion_sec: Optional[float] = None
    max_total_luminance_excursion_sec: Optional[float] = None
    max_decode_errors: int = 0  # baseline tolerance; 0 = any decode error fails
    # --- photosensitive flashes, ITU-R BT.1702 (ADR-016) ---
    check_photosensitivity: Optional[bool] = None  # opt-in: runs the extra flash-analysis decode
    max_flashes_per_second: float = 3.0  # BT.1702-3 Guideline 1: "more than three flashes ... within any one-second period"
    # --- planned-cut accuracy (ADR-017) ---
    expected_cuts: Optional[List[float]] = None  # seconds; e.g. a renderer's IR cut list
    max_cut_error_frames: float = 1.0
    allow_extra_cuts: bool = True
    cut_match_window_sec: float = 0.5
    # --- text legibility (ADR-018) ---
    text_regions: Optional[List[TextRegion]] = None
    min_contrast_ratio: float = 4.5  # WCAG 2.x AA, normal text (7.0 = AAA)
    min_text_height_px: Optional[Dict[str, float]] = None  # per role, at a 1080 px short side; None = no opinion
    # --- full-reference metrics against request.reference_video (ADR-021) ---
    reference_metrics: Optional[List[str]] = None  # opt-in: any of REFERENCE_METRICS; runs one extra two-input decode
    min_vmaf: float = 93.0  # Rassool 2017 (RealNetworks), "VMAF Reproducibility": ~93 = indistinguishable or not annoying
    min_ssim: float = 0.95  # Zinner et al. 2010 SSIM->MOS mapping (Moldovan et al. 2014, Table I): >= 0.95 = 4 (Good)
    min_psnr_db: float = 31.0  # Ke et al. 2008 PSNR->MOS mapping (Moldovan et al. 2014, Table I): >= 31 dB = 4 (Good)
    # --- stinger cut point (ADR-024) ---
    stinger: Optional[StingerRule] = None  # opt-in: alpha at the cut point; with file + cut_at, the delivered cut frames


def evaluate_video(
    measurements: MeasurementMap,
    rule: Optional[VideoRule],
    video_duration_sec: Optional[float],
    *,
    require_stream: bool = True,
) -> Tuple[List[QCCheck], List[QCFinding]]:
    """``require_stream`` defaults to True: a standalone kind="video" request
    assumes the input is meant to have a video stream. Delivery evaluation
    passes ``rule.require_video`` explicitly so an audio-only deliverable
    with ``require_video=False`` is not penalized for lacking one.
    """

    checks: List[QCCheck] = []
    findings: List[QCFinding] = []
    rule = rule or VideoRule()

    stream_present = bool(_val(measurements, "video.stream_present"))
    if not stream_present:
        status = QCStatus.FAIL if require_stream else QCStatus.PASS
        check = QCCheck("video.stream_present", "video", status, ["video.stream_present"])
        if require_stream:
            f = QCFinding("VIDEO_STREAM_MISSING", FindingSeverity.FAIL, "no video stream present", measurement_ids=["video.stream_present"])
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)
        return checks, findings  # nothing else to evaluate without a video stream
    checks.append(QCCheck("video.stream_present", "video", QCStatus.PASS, ["video.stream_present"]))

    # --- baseline: decode integrity ---
    error_count = _val(measurements, "video.decode_error_count")
    if error_count is None:
        checks.append(_unknown_check("video.decodes_without_errors", "video", "decode analysis was not performed", []))
    else:
        status = QCStatus.PASS if error_count <= rule.max_decode_errors else QCStatus.FAIL
        check = QCCheck("video.decodes_without_errors", "video", status, ["video.decode_error_count", "video.decode_errors"])
        if status == QCStatus.FAIL:
            f = QCFinding(
                "VIDEO_DECODE_ERROR", FindingSeverity.FAIL,
                f"{error_count} decode error(s) detected while decoding the video stream",
                evidence={"decode_error_count": error_count, "sample": _val(measurements, "video.decode_errors")[:5]},
                measurement_ids=["video.decode_error_count", "video.decode_errors"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- policy: duration ceiling ---
    if rule.max_duration_sec is not None:
        if video_duration_sec is None:
            checks.append(_unknown_check("video.duration_within_limit", "video", "duration could not be measured", ["container.duration_sec"]))
        else:
            status = QCStatus.PASS if video_duration_sec <= rule.max_duration_sec else QCStatus.FAIL
            check = QCCheck("video.duration_within_limit", "video", status, ["container.duration_sec"])
            if status == QCStatus.FAIL:
                f = QCFinding(
                    "VIDEO_DURATION_EXCEEDED", FindingSeverity.FAIL,
                    f"duration {video_duration_sec}s exceeds the maximum allowed {rule.max_duration_sec}s",
                    evidence={"actual": video_duration_sec, "max_allowed": rule.max_duration_sec}, measurement_ids=["container.duration_sec"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    # --- policy: resolution ---
    if rule.expected_width is not None or rule.expected_height is not None:
        width, height = _val(measurements, "video.width"), _val(measurements, "video.height")
        width_unmeasured = rule.expected_width is not None and width is None
        height_unmeasured = rule.expected_height is not None and height is None
        if width_unmeasured or height_unmeasured:
            checks.append(
                _unknown_check(
                    "video.resolution_matches_expected", "video",
                    "width/height could not be measured", ["video.width", "video.height"],
                )
            )
        else:
            ok = (rule.expected_width is None or width == rule.expected_width) and (
                rule.expected_height is None or height == rule.expected_height
            )
            status = QCStatus.PASS if ok else QCStatus.FAIL
            check = QCCheck("video.resolution_matches_expected", "video", status, ["video.width", "video.height"])
            if not ok:
                f = QCFinding(
                    "VIDEO_RESOLUTION_MISMATCH", FindingSeverity.FAIL,
                    f"resolution {width}x{height} does not match expected {rule.expected_width}x{rule.expected_height}",
                    evidence={"actual": {"width": width, "height": height}, "expected": {"width": rule.expected_width, "height": rule.expected_height}},
                    measurement_ids=["video.width", "video.height"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    # --- policy: minimum resolution ("at least" form, alongside the
    # exact-equality check above - e.g. "short side >= 1080, any exact size") ---
    if rule.min_width is not None or rule.min_height is not None:
        width, height = _val(measurements, "video.width"), _val(measurements, "video.height")
        width_unmeasured = rule.min_width is not None and width is None
        height_unmeasured = rule.min_height is not None and height is None
        if width_unmeasured or height_unmeasured:
            checks.append(
                _unknown_check(
                    "video.resolution_meets_minimum", "video",
                    "width/height could not be measured", ["video.width", "video.height"],
                )
            )
        else:
            ok = (rule.min_width is None or width >= rule.min_width) and (
                rule.min_height is None or height >= rule.min_height
            )
            status = QCStatus.PASS if ok else QCStatus.FAIL
            check = QCCheck("video.resolution_meets_minimum", "video", status, ["video.width", "video.height"])
            if not ok:
                f = QCFinding(
                    "VIDEO_RESOLUTION_BELOW_MINIMUM", FindingSeverity.FAIL,
                    f"resolution {width}x{height} is below the minimum {rule.min_width}x{rule.min_height}",
                    evidence={"actual": {"width": width, "height": height}, "expected": {"min_width": rule.min_width, "min_height": rule.min_height}},
                    measurement_ids=["video.width", "video.height"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    # --- policy: frame rate ---
    if rule.expected_frame_rate is not None:
        fps = _val(measurements, "video.frame_rate")
        if fps is None:
            checks.append(_unknown_check("video.frame_rate_matches_expected", "video", "frame rate could not be measured", ["video.frame_rate"]))
        else:
            ok = abs(fps - rule.expected_frame_rate) <= rule.frame_rate_tolerance
            status = QCStatus.PASS if ok else QCStatus.FAIL
            check = QCCheck("video.frame_rate_matches_expected", "video", status, ["video.frame_rate"])
            if not ok:
                f = QCFinding(
                    "VIDEO_FPS_MISMATCH", FindingSeverity.FAIL,
                    f"frame rate {fps} does not match expected {rule.expected_frame_rate} (tolerance {rule.frame_rate_tolerance})",
                    evidence={"actual": fps, "expected": rule.expected_frame_rate, "tolerance": rule.frame_rate_tolerance},
                    measurement_ids=["video.frame_rate"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    # --- policy: codec ---
    if rule.expected_codec is not None:
        codec = _val(measurements, "video.codec")
        _equality_check(
            checks, findings, check_id="video.codec_matches_expected", category="video",
            measurement_id="video.codec", actual=codec, expected=rule.expected_codec,
            code="VIDEO_CODEC_MISMATCH", message=f"codec {codec!r} does not match expected {rule.expected_codec!r}",
        )

    # --- policy: pixel format ---
    if rule.expected_pixel_format is not None:
        pix_fmt = _val(measurements, "video.pixel_format")
        _equality_check(
            checks, findings, check_id="video.pixel_format_matches_expected", category="video",
            measurement_id="video.pixel_format", actual=pix_fmt, expected=rule.expected_pixel_format,
            code="VIDEO_PIXEL_FORMAT_MISMATCH",
            message=f"pixel format {pix_fmt!r} does not match expected {rule.expected_pixel_format!r}",
        )

    # --- policy: aspect ratio ---
    if rule.expected_aspect_ratio is not None:
        aspect = _val(measurements, "video.aspect_ratio")
        _equality_check(
            checks, findings, check_id="video.aspect_ratio_matches_expected", category="video",
            measurement_id="video.aspect_ratio", actual=aspect, expected=rule.expected_aspect_ratio,
            code="VIDEO_ASPECT_MISMATCH",
            message=f"aspect ratio {aspect!r} does not match expected {rule.expected_aspect_ratio!r}",
        )

    # --- policy: colour / HDR characteristics (e.g. flag HDR delivered
    # where an SDR-only expectation is declared: expected_color_transfer="bt709") ---
    if rule.expected_color_range is not None:
        color_range = _val(measurements, "video.color_range")
        _equality_check(
            checks, findings, check_id="video.color_range_matches_expected", category="video",
            measurement_id="video.color_range", actual=color_range, expected=rule.expected_color_range,
            code="VIDEO_COLOR_RANGE_MISMATCH",
            message=f"color range {color_range!r} does not match expected {rule.expected_color_range!r}",
        )

    if rule.expected_color_space is not None:
        color_space = _val(measurements, "video.color_space")
        _equality_check(
            checks, findings, check_id="video.color_space_matches_expected", category="video",
            measurement_id="video.color_space", actual=color_space, expected=rule.expected_color_space,
            code="VIDEO_COLOR_SPACE_MISMATCH",
            message=f"color space {color_space!r} does not match expected {rule.expected_color_space!r}",
        )

    if rule.expected_color_transfer is not None:
        color_transfer = _val(measurements, "video.color_transfer")
        _equality_check(
            checks, findings, check_id="video.color_transfer_matches_expected", category="video",
            measurement_id="video.color_transfer", actual=color_transfer, expected=rule.expected_color_transfer,
            code="VIDEO_COLOR_TRANSFER_MISMATCH",
            message=f"color transfer {color_transfer!r} does not match expected {rule.expected_color_transfer!r}",
        )

    if rule.expected_color_primaries is not None:
        color_primaries = _val(measurements, "video.color_primaries")
        _equality_check(
            checks, findings, check_id="video.color_primaries_matches_expected", category="video",
            measurement_id="video.color_primaries", actual=color_primaries, expected=rule.expected_color_primaries,
            code="VIDEO_COLOR_PRIMARIES_MISMATCH",
            message=f"color primaries {color_primaries!r} does not match expected {rule.expected_color_primaries!r}",
        )

    # --- policy: black frames ---
    if rule.max_single_black_sec is not None or rule.max_total_black_sec is not None:
        segments = _val(measurements, "video.black_segments") or []
        total = sum(s["duration"] for s in segments if s.get("duration") is not None)
        longest = max((s["duration"] for s in segments if s.get("duration") is not None), default=0.0)
        violations = []
        if rule.max_single_black_sec is not None and longest > rule.max_single_black_sec:
            violations.append(f"longest black segment {longest}s exceeds {rule.max_single_black_sec}s")
        if rule.max_total_black_sec is not None and total > rule.max_total_black_sec:
            violations.append(f"total black duration {total}s exceeds {rule.max_total_black_sec}s")
        status = QCStatus.FAIL if violations else QCStatus.PASS
        check = QCCheck("video.black_frames_within_tolerance", "video", status, ["video.black_segments"], evidence={"total_sec": total, "longest_sec": longest})
        if violations:
            f = QCFinding(
                "VIDEO_BLACK_FRAMES_EXCEEDED", FindingSeverity.FAIL, "; ".join(violations),
                evidence={"segments": segments, "total_sec": total, "longest_sec": longest}, measurement_ids=["video.black_segments"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- policy: freeze frames ---
    if rule.max_single_freeze_sec is not None or rule.max_total_freeze_sec is not None:
        segments = _val(measurements, "video.freeze_segments") or []
        resolved = []
        unresolved = False
        for s in segments:
            duration = s.get("duration")
            if duration is None and video_duration_sec is not None and s.get("start") is not None:
                duration = video_duration_sec - s["start"]
            if duration is None:
                unresolved = True
                continue
            resolved.append(duration)
        total = sum(resolved)
        longest = max(resolved, default=0.0)
        violations = []
        if rule.max_single_freeze_sec is not None and longest > rule.max_single_freeze_sec:
            violations.append(f"longest freeze segment {longest}s exceeds {rule.max_single_freeze_sec}s")
        if rule.max_total_freeze_sec is not None and total > rule.max_total_freeze_sec:
            violations.append(f"total freeze duration {total}s exceeds {rule.max_total_freeze_sec}s")
        if violations:
            status = QCStatus.FAIL
        elif unresolved:
            status = QCStatus.UNKNOWN
        else:
            status = QCStatus.PASS
        check = QCCheck(
            "video.freeze_frames_within_tolerance", "video", status, ["video.freeze_segments"],
            evidence={"total_sec": total, "longest_sec": longest},
            reason="a freeze segment extended past the measured stream end with unknown duration" if (unresolved and not violations) else None,
        )
        if violations:
            f = QCFinding(
                "VIDEO_FREEZE_EXCEEDED", FindingSeverity.FAIL, "; ".join(violations),
                evidence={"segments": segments, "total_sec": total, "longest_sec": longest}, measurement_ids=["video.freeze_segments"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- policy: luminance-range excursions (ADR-013) ---
    if rule.max_single_luminance_excursion_sec is not None or rule.max_total_luminance_excursion_sec is not None:
        segments = _val(measurements, "video.luminance_excursions") or []
        durations = [s["duration"] for s in segments]
        total = sum(durations)
        longest = max(durations, default=0.0)
        violations = []
        if rule.max_single_luminance_excursion_sec is not None and longest > rule.max_single_luminance_excursion_sec:
            violations.append(f"longest luminance excursion {longest}s exceeds {rule.max_single_luminance_excursion_sec}s")
        if rule.max_total_luminance_excursion_sec is not None and total > rule.max_total_luminance_excursion_sec:
            violations.append(f"total luminance excursion duration {total}s exceeds {rule.max_total_luminance_excursion_sec}s")
        status = QCStatus.FAIL if violations else QCStatus.PASS
        check = QCCheck(
            "video.luminance_within_legal_range", "video", status, ["video.luminance_excursions"],
            evidence={"total_sec": total, "longest_sec": longest},
        )
        if violations:
            f = QCFinding(
                "VIDEO_LUMINANCE_OUT_OF_RANGE", FindingSeverity.FAIL, "; ".join(violations),
                evidence={"segments": segments, "total_sec": total, "longest_sec": longest}, measurement_ids=["video.luminance_excursions"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- policy: variable frame rate (mirrors ffmpeg-skill's
    # variable_frame_rate_suspected: r_frame_rate vs. avg_frame_rate) ---
    if rule.disallow_vfr:
        vfr = _val(measurements, "video.variable_frame_rate_suspected")
        if vfr is None:
            checks.append(
                _unknown_check(
                    "video.frame_rate_is_constant", "video",
                    "could not determine whether the frame rate is variable", ["video.variable_frame_rate_suspected"],
                )
            )
        else:
            status = QCStatus.FAIL if vfr else QCStatus.PASS
            check = QCCheck("video.frame_rate_is_constant", "video", status, ["video.variable_frame_rate_suspected"])
            if vfr:
                f = QCFinding(
                    "VIDEO_VARIABLE_FRAME_RATE", FindingSeverity.FAIL,
                    "a variable frame rate was detected but disallow_vfr is set",
                    evidence={"variable_frame_rate_suspected": vfr}, measurement_ids=["video.variable_frame_rate_suspected"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    # --- policy: photosensitive flashes, ITU-R BT.1702 (ADR-016) ---
    if rule.check_photosensitivity:
        _evaluate_flashes(checks, findings, measurements, rule)

    # --- policy: planned-cut accuracy (ADR-017) ---
    if rule.expected_cuts is not None:
        _evaluate_cuts(checks, findings, measurements, rule)

    # --- policy: text legibility (ADR-018) ---
    if rule.text_regions:
        _evaluate_text_regions(checks, findings, measurements, rule)

    # --- policy: full-reference metrics (ADR-021) ---
    if rule.reference_metrics:
        _evaluate_reference_metrics(checks, findings, measurements, rule)

    # --- policy: stinger cut point (ADR-024) ---
    if rule.stinger is not None:
        _evaluate_stinger(checks, findings, measurements, rule.stinger)

    return checks, findings


def _evaluate_flashes(checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: VideoRule) -> None:
    ids = ["video.flash_worst_window", "video.flash_per_second", "video.red_flash_count"]
    worst = _val(measurements, "video.flash_worst_window")
    if worst is None:
        m = measurements.get("video.flash_worst_window")
        reason = (m.notes if m is not None and m.notes else "flash analysis was not performed")
        checks.append(_unknown_check("video.photosensitive_flash_within_limit", "video", reason, ids))
        return
    limit = rule.max_flashes_per_second
    per_second = _val(measurements, "video.flash_per_second") or []
    over = [s for s in per_second if s["flashes"] > limit]
    red = "UNKNOWN (not evaluated; BT.1702-3 gives no definition of saturated red)"
    status = QCStatus.FAIL if worst["flashes"] > limit else QCStatus.PASS
    check = QCCheck(
        "video.photosensitive_flash_within_limit", "video", status, ids,
        evidence={"worst_window": worst, "max_flashes_per_second": limit, "red_flash": red},
    )
    if status == QCStatus.FAIL:
        f = QCFinding(
            "VIDEO_FLASH_RISK", FindingSeverity.FAIL,
            f"{worst['flashes']} area-qualified flashes in the 1s window starting at {worst['start']}s "
            f"exceed the limit of {limit} (ITU-R BT.1702 general flash)",
            evidence={"worst_window": worst, "seconds_over_limit": over, "max_flashes_per_second": limit, "red_flash": red},
            measurement_ids=ids,
        )
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)


def match_cuts(expected: List[float], detected: List[float], fps: float, window_sec: float) -> Dict[str, Any]:
    """Deterministic nearest-match: each expected cut, in time order,
    takes the nearest still-unmatched detected cut within ``window_sec``
    (ties go to the earlier detected cut). Pure function."""

    used = [False] * len(detected)
    matched, missing = [], []
    for e in sorted(expected):
        best_i, best_d = None, None
        for i, d in enumerate(detected):
            if used[i]:
                continue
            dist = abs(d - e)
            if dist <= window_sec + 1e-9 and (best_d is None or dist < best_d - 1e-12):
                best_i, best_d = i, dist
        if best_i is None:
            missing.append(e)
        else:
            used[best_i] = True
            matched.append({"expected": e, "detected": detected[best_i], "error_frames": round(best_d * fps, 3)})
    extra = [d for i, d in enumerate(detected) if not used[i]]
    max_error = max((m["error_frames"] for m in matched), default=0.0)
    return {"matched": matched, "missing": missing, "extra": extra, "max_error_frames": max_error}


def _evaluate_cuts(checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: VideoRule) -> None:
    ids = ["video.scene_cuts", "video.frame_rate"]
    cuts = _val(measurements, "video.scene_cuts")
    fps = _val(measurements, "video.frame_rate")
    if cuts is None or not fps:
        checks.append(_unknown_check("video.cuts_match_expected", "video", "scene cuts or frame rate could not be measured", ids))
        return
    result = match_cuts(list(rule.expected_cuts or []), [c["time"] for c in cuts], fps, rule.cut_match_window_sec)
    violations = []
    if result["missing"]:
        violations.append(f"{len(result['missing'])} expected cut(s) not found within {rule.cut_match_window_sec}s")
    if result["max_error_frames"] > rule.max_cut_error_frames:
        violations.append(f"max cut error {result['max_error_frames']} frames exceeds {rule.max_cut_error_frames}")
    if result["extra"] and not rule.allow_extra_cuts:
        violations.append(f"{len(result['extra'])} unplanned cut(s) detected and allow_extra_cuts is false")
    evidence = {**result, "max_cut_error_frames": rule.max_cut_error_frames, "allow_extra_cuts": rule.allow_extra_cuts}
    status = QCStatus.FAIL if violations else QCStatus.PASS
    check = QCCheck("video.cuts_match_expected", "video", status, ids, evidence=evidence)
    if violations:
        f = QCFinding("VIDEO_CUT_MISMATCH", FindingSeverity.FAIL, "; ".join(violations), evidence=evidence, measurement_ids=ids)
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)


def _evaluate_text_regions(checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: VideoRule) -> None:
    ids = ["video.text_regions"]
    regions = _val(measurements, "video.text_regions")
    if regions is None:
        checks.append(_unknown_check("video.text_legible", "video", "text regions could not be measured", ids))
        return
    min_heights = rule.min_text_height_px or {}
    failing, unmeasured = [], []
    for r in regions:
        if not r.get("measured"):
            unmeasured.append({"index": r["index"], "reason": r.get("reason")})
            continue
        problems = []
        if r["contrast_ratio"] < rule.min_contrast_ratio:
            problems.append(f"contrast {r['contrast_ratio']}:1 < {rule.min_contrast_ratio}:1")
        floor = min_heights.get(r["role"])
        if floor is not None:
            height = r.get("text_height_px_at_1080")
            if height is None:
                unmeasured.append({"index": r["index"], "reason": "text height at 1080 could not be computed"})
            elif height < floor:
                problems.append(f"text height {height}px@1080 < {floor}px ({r['role']})")
        if problems:
            failing.append({"index": r["index"], "t": r["t"], "role": r["role"], "problems": problems})
    evidence = {"failing": failing, "unmeasured": unmeasured, "min_contrast_ratio": rule.min_contrast_ratio, "min_text_height_px": min_heights}
    if failing:
        status = QCStatus.FAIL
    elif unmeasured:
        status = QCStatus.UNKNOWN
    else:
        status = QCStatus.PASS
    check = QCCheck(
        "video.text_legible", "video", status, ids, evidence=evidence,
        reason="one or more text regions could not be measured" if status == QCStatus.UNKNOWN else None,
    )
    if failing:
        f = QCFinding(
            "TEXT_ILLEGIBLE", FindingSeverity.FAIL,
            f"{len(failing)} text region(s) below the contrast/height minimum",
            evidence=evidence, measurement_ids=ids,
        )
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)


def _reference_metric_check(
    checks: List[QCCheck],
    findings: List[QCFinding],
    measurements: MeasurementMap,
    *,
    check_id: str,
    measurement_id: str,
    minimum: float,
    code: str,
    label: str,
) -> None:
    """One full-reference score against its minimum (ADR-021). An
    unmeasured score (libvmaf missing, reference refused, no reference) is
    UNKNOWN with the measurement's own reason - never PASS, never FAIL."""

    ids = [measurement_id, "video.reference_alignment"]
    alignment = _val(measurements, "video.reference_alignment")
    value = _val(measurements, measurement_id)
    if value is None:
        m = measurements.get(measurement_id)
        reason = m.notes if m is not None and m.notes else f"{label} could not be measured"
        check = _unknown_check(check_id, "video", reason, ids)
        check.evidence = {"minimum": minimum, "alignment": alignment}
        checks.append(check)
        return
    status = QCStatus.PASS if value >= minimum else QCStatus.FAIL
    evidence = {"value": value, "minimum": minimum, "alignment": alignment}
    check = QCCheck(check_id, "video", status, ids, evidence=evidence)
    if status == QCStatus.FAIL:
        f = QCFinding(
            code, FindingSeverity.FAIL,
            f"{label} {value} against the reference is below the minimum {minimum}",
            evidence=evidence, measurement_ids=ids,
        )
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)


def _evaluate_reference_metrics(checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: VideoRule) -> None:
    wanted = set(rule.reference_metrics or [])
    if "vmaf" in wanted:
        _reference_metric_check(
            checks, findings, measurements, check_id="video.vmaf_meets_minimum", measurement_id="video.vmaf",
            minimum=rule.min_vmaf, code="VIDEO_VMAF_BELOW_MINIMUM", label="VMAF",
        )
    if "ssim" in wanted:
        _reference_metric_check(
            checks, findings, measurements, check_id="video.ssim_meets_minimum", measurement_id="video.ssim",
            minimum=rule.min_ssim, code="VIDEO_SSIM_BELOW_MINIMUM", label="SSIM",
        )
    if "psnr" in wanted:
        _reference_metric_check(
            checks, findings, measurements, check_id="video.psnr_meets_minimum", measurement_id="video.psnr_db",
            minimum=rule.min_psnr_db, code="VIDEO_PSNR_BELOW_MINIMUM", label="PSNR (dB)",
        )


def _evaluate_stinger(checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: StingerRule) -> None:
    """ADR-024. The declared cover window, the measured alpha at the cut
    point, and (with ``cut_at``) the delivered frames at the cuts - each
    its own check, so a stinger.json that names the wrong frame, a graphic
    with a hole, and a stinger placed off the cut are told apart."""

    cut_point = rule.cut_point
    if rule.cover is not None:
        cover = {"start": rule.cover.start, "end": rule.cover.end}
        inside = rule.cover.start <= cut_point < rule.cover.end
        evidence = {"cut_point": cut_point, "cover": cover}
        check = QCCheck("video.stinger_cut_point_in_cover", "video", QCStatus.PASS if inside else QCStatus.FAIL, [], evidence=evidence)
        if not inside:
            f = QCFinding(
                "VIDEO_STINGER_CUT_POINT_OUTSIDE_COVER", FindingSeverity.FAIL,
                f"{_at(cut_point)} the stated cut point is outside the stated cover window "
                f"[{rule.cover.start}, {rule.cover.end})s",
                evidence=evidence, timestamp=cut_point,
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    ids = ["video.stinger_cut_point_alpha"]
    alpha = _val(measurements, "video.stinger_cut_point_alpha")
    if not alpha or not alpha.get("measured"):
        reason = (alpha or {}).get("reason") or "the stinger's alpha at its cut point was not measured"
        check = _unknown_check("video.stinger_opaque_at_cut_point", "video", reason, ids)
        check.evidence = {"alpha": alpha}
        checks.append(check)
    else:
        covered = alpha["pixels_below"] == 0
        check = QCCheck(
            "video.stinger_opaque_at_cut_point", "video", QCStatus.PASS if covered else QCStatus.FAIL, ids,
            evidence={"alpha": alpha},
        )
        if not covered:
            f = QCFinding(
                "VIDEO_STINGER_NOT_OPAQUE", FindingSeverity.FAIL,
                f"{_at(cut_point)} frame {alpha['frame']} of the stinger: {alpha['pixels_below']} of {alpha['pixels']} pixels "
                f"have alpha below {OPAQUE_MIN_ALPHA}/255 (lowest {alpha['min_alpha_255']}/255), so the picture under it shows through",
                evidence={"alpha": alpha}, measurement_ids=ids, timestamp=cut_point,
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.cut_at:
        _evaluate_stinger_cut_frames(checks, findings, measurements, rule)


def _evaluate_stinger_cut_frames(checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: StingerRule) -> None:
    ids = ["video.stinger_cut_frames"]
    frames = _val(measurements, "video.stinger_cut_frames")
    limit = rule.max_cut_frame_difference
    if not frames:
        checks.append(_unknown_check("video.stinger_cut_frame_matches", "video", "the delivered cut frames were not measured", ids))
        return
    failing, unmeasured = [], []
    for c in frames.get("cuts", []):
        if not c.get("measured"):
            unmeasured.append({"cut_at": c["cut_at"], "reason": c.get("reason")})
        elif c["difference"] > limit:
            failing.append({
                **c, "time_sec": c["cut_at"],
                "description": (
                    f"{_at(c['cut_at'])} frame {c['frame']} differs from the stinger's frame at its cut point by "
                    f"{c['difference']} (mean |RGB|, 0-255), more than {limit}"
                ),
            })
    evidence = {
        "stinger_frame": frames.get("stinger_frame"), "cuts": frames.get("cuts", []),
        "max_cut_frame_difference": limit, "violations": failing, "unmeasured": unmeasured,
    }
    if failing:
        status = QCStatus.FAIL
    elif unmeasured:
        status = QCStatus.UNKNOWN
    else:
        status = QCStatus.PASS
    check = QCCheck(
        "video.stinger_cut_frame_matches", "video", status, ids, evidence=evidence,
        reason=("; ".join(f"{u['cut_at']}s: {u['reason']}" for u in unmeasured) if status == QCStatus.UNKNOWN else None),
    )
    if failing:
        f = QCFinding(
            "VIDEO_STINGER_CUT_FRAME_MISMATCH", FindingSeverity.FAIL,
            f"{len(failing)} cut frame(s) do not show the stinger's frame at its cut point (limit {limit})",
            evidence=evidence, measurement_ids=ids, timestamp=failing[0]["cut_at"],
        )
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


@dataclass
class LoopSeamRule:
    """A loop seam to check (ADR-025): the file is declared loopable, so
    its end runs into its start (``include_wrap``), and ``seams`` lists
    seam times inside it (a bed repeated N times). Every limit is opt-in
    and has no default; ``window_sec`` (the level/spectrum windows on
    each side of a seam) is needed by the level and spectrum limits."""

    window_sec: Optional[float] = None
    seams: Optional[List[float]] = None
    include_wrap: bool = True
    max_click_ratio: Optional[float] = None
    max_level_diff_db: Optional[float] = None
    max_band_diff_db: Optional[float] = None


@dataclass
class AudioRule:
    require_audio_stream: Optional[bool] = None
    expected_sample_rate: Optional[int] = None
    expected_channels: Optional[int] = None
    expected_channel_layout: Optional[str] = None
    max_leading_silence_sec: Optional[float] = None
    max_trailing_silence_sec: Optional[float] = None
    max_internal_silence_sec: Optional[float] = None
    integrated_loudness_target_lufs: Optional[float] = None
    integrated_loudness_tolerance_lu: float = 1.0
    max_true_peak_dbfs: Optional[float] = None
    max_loudness_range_lu: Optional[float] = None
    max_channel_level_diff_db: Optional[float] = None
    max_decode_errors: int = 0
    loop_seam: Optional[LoopSeamRule] = None  # opt-in: one extra decode to float samples (ADR-025)


def evaluate_audio(measurements: MeasurementMap, rule: Optional[AudioRule]) -> Tuple[List[QCCheck], List[QCFinding]]:
    checks: List[QCCheck] = []
    findings: List[QCFinding] = []
    rule = rule or AudioRule()

    stream_present = _val(measurements, "audio.stream_present")

    if rule.require_audio_stream is not None:
        ok = stream_present == rule.require_audio_stream
        status = QCStatus.PASS if ok else QCStatus.FAIL
        check = QCCheck("audio.stream_present_matches_expected", "audio", status, ["audio.stream_present"])
        if not ok:
            f = QCFinding(
                "AUDIO_STREAM_MISSING" if rule.require_audio_stream else "AUDIO_STREAM_UNEXPECTED",
                FindingSeverity.FAIL,
                f"expected audio stream present={rule.require_audio_stream}, actual={stream_present}",
                measurement_ids=["audio.stream_present"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if not stream_present:
        return checks, findings

    # --- baseline: decode integrity ---
    error_count = _val(measurements, "audio.decode_error_count")
    if error_count is None:
        checks.append(_unknown_check("audio.decodes_without_errors", "audio", "decode analysis was not performed", []))
    else:
        status = QCStatus.PASS if error_count <= rule.max_decode_errors else QCStatus.FAIL
        check = QCCheck("audio.decodes_without_errors", "audio", status, ["audio.decode_error_count", "audio.decode_errors"])
        if status == QCStatus.FAIL:
            f = QCFinding(
                "AUDIO_DECODE_ERROR", FindingSeverity.FAIL,
                f"{error_count} decode error(s) detected while decoding the audio stream",
                evidence={"decode_error_count": error_count}, measurement_ids=["audio.decode_error_count"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- baseline: clipping ---
    clipping = _val(measurements, "audio.clipping_detected")
    if clipping is None:
        checks.append(_unknown_check("audio.no_clipping", "audio", "loudness/clipping analysis was not performed", []))
    else:
        status = QCStatus.FAIL if clipping else QCStatus.PASS
        check = QCCheck("audio.no_clipping", "audio", status, ["audio.clipping_detected", "audio.peak_level_dbfs"])
        if clipping:
            f = QCFinding(
                "AUDIO_CLIPPING_DETECTED", FindingSeverity.FAIL, "digital clipping detected (peak reached full scale)",
                evidence={"peak_level_dbfs": _val(measurements, "audio.peak_level_dbfs")}, measurement_ids=["audio.peak_level_dbfs"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- policy: sample rate / channels / layout ---
    if rule.expected_sample_rate is not None:
        sr = _val(measurements, "audio.sample_rate")
        _equality_check(
            checks, findings, check_id="audio.sample_rate_matches_expected", category="audio",
            measurement_id="audio.sample_rate", actual=sr, expected=rule.expected_sample_rate,
            code="AUDIO_SAMPLE_RATE_MISMATCH", message=f"sample rate {sr} does not match expected {rule.expected_sample_rate}",
        )

    if rule.expected_channels is not None:
        channels = _val(measurements, "audio.channels")
        _equality_check(
            checks, findings, check_id="audio.channels_match_expected", category="audio",
            measurement_id="audio.channels", actual=channels, expected=rule.expected_channels,
            code="AUDIO_CHANNELS_MISMATCH", message=f"channel count {channels} does not match expected {rule.expected_channels}",
        )

    if rule.expected_channel_layout is not None:
        layout = _val(measurements, "audio.channel_layout")
        _equality_check(
            checks, findings, check_id="audio.channel_layout_matches_expected", category="audio",
            measurement_id="audio.channel_layout", actual=layout, expected=rule.expected_channel_layout,
            code="AUDIO_CHANNEL_LAYOUT_MISMATCH",
            message=f"channel layout {layout!r} does not match expected {rule.expected_channel_layout!r}",
        )

    # --- policy: silence ---
    if rule.max_leading_silence_sec is not None:
        leading = _val(measurements, "audio.leading_silence_sec") or 0.0
        status = QCStatus.PASS if leading <= rule.max_leading_silence_sec else QCStatus.FAIL
        check = QCCheck("audio.leading_silence_within_tolerance", "audio", status, ["audio.leading_silence_sec"])
        if status == QCStatus.FAIL:
            f = QCFinding(
                "AUDIO_LEADING_SILENCE_EXCEEDED", FindingSeverity.WARN,
                f"leading silence {leading}s exceeds {rule.max_leading_silence_sec}s",
                evidence={"actual": leading, "max_allowed": rule.max_leading_silence_sec}, measurement_ids=["audio.leading_silence_sec"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.max_trailing_silence_sec is not None:
        trailing = _val(measurements, "audio.trailing_silence_sec") or 0.0
        status = QCStatus.PASS if trailing <= rule.max_trailing_silence_sec else QCStatus.FAIL
        check = QCCheck("audio.trailing_silence_within_tolerance", "audio", status, ["audio.trailing_silence_sec"])
        if status == QCStatus.FAIL:
            f = QCFinding(
                "AUDIO_TRAILING_SILENCE_EXCEEDED", FindingSeverity.WARN,
                f"trailing silence {trailing}s exceeds {rule.max_trailing_silence_sec}s",
                evidence={"actual": trailing, "max_allowed": rule.max_trailing_silence_sec}, measurement_ids=["audio.trailing_silence_sec"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.max_internal_silence_sec is not None:
        internal = _val(measurements, "audio.internal_silence_segments") or []
        longest = max((s["duration"] for s in internal if s.get("duration") is not None), default=0.0)
        status = QCStatus.PASS if longest <= rule.max_internal_silence_sec else QCStatus.FAIL
        check = QCCheck("audio.internal_silence_within_tolerance", "audio", status, ["audio.internal_silence_segments"])
        if status == QCStatus.FAIL:
            f = QCFinding(
                "AUDIO_INTERNAL_SILENCE_EXCEEDED", FindingSeverity.WARN,
                f"unexpected internal silence of {longest}s exceeds {rule.max_internal_silence_sec}s",
                evidence={"segments": internal, "longest_sec": longest}, measurement_ids=["audio.internal_silence_segments"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- policy: loudness ---
    if rule.integrated_loudness_target_lufs is not None:
        lufs = _val(measurements, "audio.integrated_loudness_lufs")
        if lufs is None or lufs == float("-inf"):
            checks.append(_unknown_check("audio.integrated_loudness_within_tolerance", "audio", "integrated loudness could not be measured (silent or unmeasurable stream)", ["audio.integrated_loudness_lufs"]))
        else:
            diff = abs(lufs - rule.integrated_loudness_target_lufs)
            status = QCStatus.PASS if diff <= rule.integrated_loudness_tolerance_lu else QCStatus.FAIL
            check = QCCheck("audio.integrated_loudness_within_tolerance", "audio", status, ["audio.integrated_loudness_lufs"])
            if status == QCStatus.FAIL:
                f = QCFinding(
                    "AUDIO_LOUDNESS_OUT_OF_RANGE", FindingSeverity.FAIL,
                    f"integrated loudness {lufs} LUFS differs from target {rule.integrated_loudness_target_lufs} LUFS by {diff} LU (tolerance {rule.integrated_loudness_tolerance_lu} LU)",
                    evidence={"actual_lufs": lufs, "target_lufs": rule.integrated_loudness_target_lufs, "tolerance_lu": rule.integrated_loudness_tolerance_lu},
                    measurement_ids=["audio.integrated_loudness_lufs"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    if rule.max_true_peak_dbfs is not None:
        tp = _val(measurements, "audio.true_peak_dbfs")
        if tp is None:
            checks.append(_unknown_check("audio.true_peak_within_tolerance", "audio", "true peak could not be measured", ["audio.true_peak_dbfs"]))
        else:
            status = QCStatus.PASS if tp <= rule.max_true_peak_dbfs else QCStatus.FAIL
            check = QCCheck("audio.true_peak_within_tolerance", "audio", status, ["audio.true_peak_dbfs"])
            if status == QCStatus.FAIL:
                f = QCFinding(
                    "AUDIO_TRUE_PEAK_EXCEEDED", FindingSeverity.FAIL,
                    f"true peak {tp} dBFS exceeds maximum allowed {rule.max_true_peak_dbfs} dBFS",
                    evidence={"actual": tp, "max_allowed": rule.max_true_peak_dbfs}, measurement_ids=["audio.true_peak_dbfs"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    if rule.max_loudness_range_lu is not None:
        lra = _val(measurements, "audio.loudness_range_lu")
        if lra is None:
            checks.append(_unknown_check("audio.loudness_range_within_tolerance", "audio", "loudness range could not be measured", ["audio.loudness_range_lu"]))
        else:
            status = QCStatus.PASS if lra <= rule.max_loudness_range_lu else QCStatus.FAIL
            check = QCCheck("audio.loudness_range_within_tolerance", "audio", status, ["audio.loudness_range_lu"])
            if status == QCStatus.FAIL:
                f = QCFinding(
                    "AUDIO_LOUDNESS_RANGE_EXCEEDED", FindingSeverity.WARN,
                    f"loudness range {lra} LU exceeds maximum allowed {rule.max_loudness_range_lu} LU",
                    evidence={"actual": lra, "max_allowed": rule.max_loudness_range_lu}, measurement_ids=["audio.loudness_range_lu"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    # --- policy: channel imbalance / missing channel ---
    if rule.max_channel_level_diff_db is not None:
        per_channel = _val(measurements, "audio.per_channel_levels") or []
        rms_values = [c["rms_level_dbfs"] for c in per_channel if isinstance(c.get("rms_level_dbfs"), (int, float))]
        if len(rms_values) < 2:
            checks.append(_unknown_check("audio.channel_balance_within_tolerance", "audio", "fewer than two channels with measurable levels", ["audio.per_channel_levels"]))
        else:
            diff = max(rms_values) - min(rms_values)
            status = QCStatus.PASS if diff <= rule.max_channel_level_diff_db else QCStatus.FAIL
            check = QCCheck("audio.channel_balance_within_tolerance", "audio", status, ["audio.per_channel_levels"], evidence={"rms_diff_db": diff})
            if status == QCStatus.FAIL:
                code = "AUDIO_CHANNEL_MISSING" if min(rms_values) <= -90.0 else "AUDIO_CHANNEL_IMBALANCE"
                f = QCFinding(
                    code, FindingSeverity.FAIL,
                    f"channel level difference {diff} dB exceeds maximum allowed {rule.max_channel_level_diff_db} dB",
                    evidence={"per_channel": per_channel, "diff_db": diff}, measurement_ids=["audio.per_channel_levels"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    # --- policy: loop seam (ADR-025) ---
    if rule.loop_seam is not None:
        _evaluate_loop_seams(checks, findings, measurements, rule.loop_seam)

    return checks, findings


def _seam_label(seam: Dict[str, Any]) -> str:
    if seam["seam"] == "wrap":
        return f"{_at(seam['time_sec'])} wrap (end -> start)"
    return f"{_at(seam['time_sec'])} seam"


def _click_description(seam: Dict[str, Any], c: Dict[str, Any], limit: float) -> str:
    if c["ratio"] is None:
        return f"{_seam_label(seam)}: a click out of digital silence on channel {c['channel']} (second difference {c['peak']})"
    return (
        f"{_seam_label(seam)}: click ratio {c['ratio']} on channel {c['channel']} (second difference {c['peak']} "
        f"against {c['reference']} around it) exceeds {limit}"
    )


def _loop_seam_violation(fact: str, seam: Dict[str, Any], m: Dict[str, Any], limit: float) -> Optional[str]:
    """The description of a violation, or None when within the limit
    (exactly the limit passes)."""

    if fact == "click":
        if m["ratio"] is None or m["ratio"] > limit:
            return _click_description(seam, m, limit)
        return None
    if fact == "level":
        if m["diff_db"] > limit:
            return (
                f"{_seam_label(seam)}: level {m['before_dbfs']} dBFS before and {m['after_dbfs']} dBFS after on channel "
                f"{m['channel']}, {m['diff_db']} dB apart, more than {limit} dB"
            )
        return None
    if m["max_diff_db"] is None:
        return (
            f"{_seam_label(seam)}: channel {m['channel']} is digital silence {m['silent_side']} the seam and not on the "
            f"other side, so the spectrum does not continue across it"
        )
    if m["max_diff_db"] > limit:
        worst = max((b for b in m["bands"] if b["compared"]), key=lambda b: b["diff_db"])
        return (
            f"{_seam_label(seam)}: on channel {m['channel']} the {worst['centre_hz']:g} Hz octave band is {worst['before_db']} dB "
            f"before and {worst['after_db']} dB after (relative to each side's total), {worst['diff_db']} dB apart, "
            f"more than {limit} dB"
        )
    return None


def _loop_seam_check(
    checks: List[QCCheck], findings: List[QCFinding], value: Optional[Dict[str, Any]], limit: Optional[float], *,
    field_name: str, check_id: str, fact: str, code: str, key: str,
) -> None:
    """One limit over every seam (ADR-025). A seam that could not be
    measured for this fact makes the check UNKNOWN unless a seam fails."""

    if limit is None:
        return
    ids = ["audio.loop_seams"]
    if not value or not value.get("measured"):
        reason = (value or {}).get("reason") or "the loop seams were not measured"
        checks.append(_unknown_check(check_id, "audio", reason, ids))
        return
    violations, unmeasured, measured = [], [], []
    for seam in value["seams"]:
        m = seam[fact]
        if not m.get("measured"):
            unmeasured.append({"seam": seam["seam"], "time_sec": seam["time_sec"], "reason": m.get("reason")})
            continue
        measured.append((math.inf if m[key] is None else m[key], seam["seam"], m[key]))
        description = _loop_seam_violation(fact, seam, m, limit)
        if description is not None:
            details = {k: v for k, v in m.items() if k not in ("measured", "bands")}
            violations.append({"seam": seam["seam"], "time_sec": seam["time_sec"], **details, "description": description})
    violations = _sorted_by_time(violations)
    worst = max(measured, key=lambda t: t[0], default=None)
    evidence = {
        "limit": limit,
        "seams": [{"seam": s["seam"], "time_sec": s["time_sec"], fact: s[fact]} for s in value["seams"]],
        "worst": None if worst is None else {"seam": worst[1], key: worst[2]},
        "violations": violations, "unmeasured": unmeasured,
    }
    if violations:
        status = QCStatus.FAIL
    elif unmeasured:
        status = QCStatus.UNKNOWN
    else:
        status = QCStatus.PASS
    check = QCCheck(
        check_id, "audio", status, ids, evidence=evidence,
        reason=("; ".join(f"{u['seam']}: {u['reason']}" for u in unmeasured) if status == QCStatus.UNKNOWN else None),
    )
    if violations:
        f = QCFinding(
            code, FindingSeverity.FAIL,
            f"{len(violations)} loop seam(s) over the {field_name} limit {limit}; first: {violations[0]['description']}",
            evidence=evidence, measurement_ids=ids, timestamp=violations[0]["time_sec"],
        )
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)


def _evaluate_loop_seams(checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: LoopSeamRule) -> None:
    """ADR-025: one check per limit the rule sets."""

    value = _val(measurements, "audio.loop_seams")
    _loop_seam_check(
        checks, findings, value, rule.max_click_ratio, field_name="max_click_ratio",
        check_id="audio.loop_seam_click_within_limit", fact="click", code="AUDIO_LOOP_SEAM_CLICK", key="ratio",
    )
    _loop_seam_check(
        checks, findings, value, rule.max_level_diff_db, field_name="max_level_diff_db",
        check_id="audio.loop_seam_level_within_limit", fact="level", code="AUDIO_LOOP_SEAM_LEVEL_MISMATCH", key="diff_db",
    )
    _loop_seam_check(
        checks, findings, value, rule.max_band_diff_db, field_name="max_band_diff_db",
        check_id="audio.loop_seam_spectrum_within_limit", fact="spectrum", code="AUDIO_LOOP_SEAM_SPECTRUM_MISMATCH",
        key="max_diff_db",
    )


# ---------------------------------------------------------------------------
# Subtitle
# ---------------------------------------------------------------------------


@dataclass
class TimelineSegment:
    """One caller-supplied source-to-delivery time mapping (ADR-012).

    qc-skill never constructs, infers, or reconstructs these - they come
    from whatever the agent already knows about its own edit history.
    ``[source_start, source_end)`` of the *source* timeline appears in the
    delivery starting at ``delivery_start``, played at ``speed``x (2.0 =
    twice as fast, so half the source duration on the delivery timeline).
    """

    source_start: float
    source_end: float
    delivery_start: float
    speed: float = 1.0


@dataclass
class SourceCue:
    """One cue's timing as authored against the *source* timeline, before
    whatever trim/concat/speed edit produced the delivery."""

    start: float
    end: float


@dataclass
class TimelineIntegrityRule:
    timeline: List[TimelineSegment] = field(default_factory=list)
    source_cues: List[SourceCue] = field(default_factory=list)
    tolerance_sec: float = 0.1


def _map_source_to_delivery(timeline: List[TimelineSegment], source_time: float) -> Optional[float]:
    for segment in timeline:
        if segment.source_start <= source_time <= segment.source_end:
            speed = segment.speed or 1.0
            return segment.delivery_start + (source_time - segment.source_start) / speed
    return None  # source_time falls in a cut region - not covered by any segment


@dataclass
class CoveredInterval:
    """A caller-named time range in which speech counts as covered even
    with no cue on screen (ADR-023), e.g. an emphasis graphic that shows
    the spoken words. ``label`` is carried into evidence only."""

    start: float
    end: float
    label: Optional[str] = None


# Coarse part-of-speech labels a transcript word may carry in ``pos``
# (ADR-023). qc-skill never assigns one itself.
POS_TAGS = (
    "noun", "pronoun", "verb", "adjective", "adverb", "particle", "auxiliary",
    "conjunction", "interjection", "prefix", "suffix", "symbol", "other",
)

# Opt-in bundles of suggested SubtitleRule values (ADR-023), applied only
# when a rule names one in ``preset``, and only to fields the rule leaves
# unset. They are style suggestions, not standards: the default (no
# preset) assumes no language at all.
SUBTITLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "ja": {
        "forbidden_line_start_pos": ["particle", "auxiliary"],
        "forbidden_line_start_phrases": [
            "を", "に", "が", "って", "けど", "ので", "です", "ます", "でした", "ました", "と思います",
        ],
        "max_total_chars": 32,  # two lines of 16
        "min_cue_chars": 4,  # WARN only: short replies are legitimate
    },
}


@dataclass
class SubtitleRule:
    require_subtitle: Optional[bool] = None
    max_line_length: Optional[int] = None
    max_cue_duration_sec: Optional[float] = None
    max_gap_sec: Optional[float] = None
    max_duration_delta_sec: Optional[float] = None
    min_coverage_ratio: Optional[float] = None
    allow_overlapping_cues: bool = False
    allow_duplicate_ids: bool = False
    timeline_integrity: Optional[TimelineIntegrityRule] = None
    # --- cue text and subtitle-vs-speech checks (ADR-023); every one is opt-in ---
    preset: Optional[str] = None  # a SUBTITLE_PRESETS key, e.g. "ja"; fills only the fields left unset
    transcript: Optional[str] = None  # path to a word-timed transcript JSON
    word_boundary_tolerance_sec: Optional[float] = None
    max_cue_lead_sec: Optional[float] = None
    max_uncovered_speech_sec: Optional[float] = None
    extra_covered_intervals: Optional[List[CoveredInterval]] = None
    forbidden_line_start_pos: Optional[List[str]] = None  # any of POS_TAGS
    forbidden_line_start_phrases: Optional[List[str]] = None
    max_total_chars: Optional[int] = None
    min_cue_chars: Optional[int] = None


def resolve_subtitle_preset(rule: SubtitleRule) -> SubtitleRule:
    """``rule`` with its named preset's values filled into the fields it
    leaves unset (an explicit value always wins)."""

    if rule.preset is None:
        return rule
    values = SUBTITLE_PRESETS[rule.preset]
    fill = {k: (list(v) if isinstance(v, list) else v) for k, v in values.items() if getattr(rule, k) is None}
    return replace(rule, **fill) if fill else rule


def subtitle_rule_needs_cue_texts(rule: Optional[SubtitleRule]) -> bool:
    if rule is None:
        return False
    rule = resolve_subtitle_preset(rule)
    return any(
        value is not None
        for value in (
            rule.word_boundary_tolerance_sec, rule.max_cue_lead_sec, rule.max_uncovered_speech_sec,
            rule.forbidden_line_start_pos, rule.forbidden_line_start_phrases,
            rule.max_total_chars, rule.min_cue_chars,
        )
    )


def _evaluate_timeline_integrity(
    measurements: MeasurementMap, rule: TimelineIntegrityRule
) -> Tuple[QCCheck, Optional[QCFinding]]:
    """Does this subtitle's delivery-timeline cue timing match what the
    caller-supplied ``timeline`` says it should be, given the cue timing
    the caller asserts existed on the *source* timeline (ADR-012)?

    Only ever compares supplied data against ``subtitle.cues`` (observed
    fact) - never reconstructs a timeline, never infers which cues were
    cut.
    """

    actual_cues = _val(measurements, "subtitle.cues") or []

    if len(actual_cues) != len(rule.source_cues):
        evidence = {"actual_cue_count": len(actual_cues), "expected_cue_count": len(rule.source_cues)}
        finding = QCFinding(
            "SUBTITLE_TIMELINE_CUE_COUNT_MISMATCH", FindingSeverity.FAIL,
            f"delivery has {len(actual_cues)} cue(s) but the supplied timeline mapping expects {len(rule.source_cues)}",
            evidence=evidence, measurement_ids=["subtitle.cues"],
        )
        check = QCCheck("subtitle.timeline_mapping_matches_source", "subtitle", QCStatus.FAIL, ["subtitle.cues"], evidence=evidence, finding_codes=[finding.code])
        return check, finding

    mismatches = []
    unresolved = []
    for i, (source_cue, actual) in enumerate(zip(rule.source_cues, actual_cues)):
        expected_start = _map_source_to_delivery(rule.timeline, source_cue.start)
        expected_end = _map_source_to_delivery(rule.timeline, source_cue.end)
        if expected_start is None or expected_end is None:
            unresolved.append(i)
            continue
        actual_start, actual_end = actual.get("start"), actual.get("end")
        if (
            actual_start is None or actual_end is None
            or abs(actual_start - expected_start) > rule.tolerance_sec
            or abs(actual_end - expected_end) > rule.tolerance_sec
        ):
            mismatches.append({
                "cue_index": i,
                "expected": {"start": expected_start, "end": expected_end},
                "actual": {"start": actual_start, "end": actual_end},
            })

    evidence = {"mismatches": mismatches, "unresolved_source_cues": unresolved}
    if mismatches:
        finding = QCFinding(
            "SUBTITLE_TIMELINE_MAPPING_MISMATCH", FindingSeverity.FAIL,
            f"{len(mismatches)} cue(s) do not match their expected delivery-timeline position under the supplied timeline mapping",
            evidence=evidence, measurement_ids=["subtitle.cues"],
        )
        return QCCheck("subtitle.timeline_mapping_matches_source", "subtitle", QCStatus.FAIL, ["subtitle.cues"], evidence=evidence, finding_codes=[finding.code]), finding
    if unresolved:
        reason = f"source cue(s) at index {unresolved} fall outside every timeline segment (a cut region) - cannot verify"
        return QCCheck("subtitle.timeline_mapping_matches_source", "subtitle", QCStatus.UNKNOWN, ["subtitle.cues"], evidence=evidence, reason=reason), None
    return QCCheck("subtitle.timeline_mapping_matches_source", "subtitle", QCStatus.PASS, ["subtitle.cues"], evidence=evidence), None


def evaluate_subtitle(measurements: MeasurementMap, rule: Optional[SubtitleRule]) -> Tuple[List[QCCheck], List[QCFinding]]:
    checks: List[QCCheck] = []
    findings: List[QCFinding] = []
    rule = resolve_subtitle_preset(rule or SubtitleRule())

    exists = _val(measurements, "subtitle.exists")
    if rule.require_subtitle is not None:
        ok = bool(exists) == rule.require_subtitle
        status = QCStatus.PASS if ok else QCStatus.FAIL
        check = QCCheck("subtitle.presence_matches_expected", "subtitle", status, ["subtitle.exists"])
        if not ok:
            f = QCFinding(
                "SUBTITLE_MISSING", FindingSeverity.FAIL,
                f"expected subtitle presence={rule.require_subtitle}, actual={bool(exists)}",
                measurement_ids=["subtitle.exists"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if not exists:
        return checks, findings

    # --- baseline: structurally malformed cues are always defects ---
    invalid_ts = _val(measurements, "subtitle.invalid_timestamps") or []
    status = QCStatus.FAIL if invalid_ts else QCStatus.PASS
    check = QCCheck("subtitle.timestamps_valid", "subtitle", status, ["subtitle.invalid_timestamps"])
    if invalid_ts:
        f = QCFinding("SUBTITLE_INVALID_TIMESTAMP", FindingSeverity.FAIL, f"{len(invalid_ts)} cue(s) have invalid timestamps", evidence={"cues": invalid_ts}, measurement_ids=["subtitle.invalid_timestamps"])
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)

    empty = _val(measurements, "subtitle.empty_cues") or []
    status = QCStatus.WARN if empty else QCStatus.PASS
    check = QCCheck("subtitle.no_empty_cues", "subtitle", status, ["subtitle.empty_cues"])
    if empty:
        f = QCFinding("SUBTITLE_EMPTY_CUE", FindingSeverity.WARN, f"{len(empty)} cue(s) have no visible text", evidence={"cue_indices": empty}, measurement_ids=["subtitle.empty_cues"])
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)

    dupes = _val(measurements, "subtitle.duplicate_ids") or []
    if not rule.allow_duplicate_ids:
        status = QCStatus.FAIL if dupes else QCStatus.PASS
        check = QCCheck("subtitle.no_duplicate_ids", "subtitle", status, ["subtitle.duplicate_ids"])
        if dupes:
            f = QCFinding("SUBTITLE_DUPLICATE_ID", FindingSeverity.FAIL, f"duplicate cue identifiers: {dupes}", evidence={"ids": dupes}, measurement_ids=["subtitle.duplicate_ids"])
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    control_chars = _val(measurements, "subtitle.invalid_control_characters") or []
    status = QCStatus.FAIL if control_chars else QCStatus.PASS
    check = QCCheck("subtitle.no_control_characters", "subtitle", status, ["subtitle.invalid_control_characters"])
    if control_chars:
        f = QCFinding("SUBTITLE_CONTROL_CHARACTER", FindingSeverity.FAIL, f"{len(control_chars)} cue(s) contain control characters", evidence={"cue_indices": control_chars}, measurement_ids=["subtitle.invalid_control_characters"])
        findings.append(f)
        check.finding_codes.append(f.code)
    checks.append(check)

    if not rule.allow_overlapping_cues:
        overlaps = _val(measurements, "subtitle.overlapping_cues") or []
        status = QCStatus.FAIL if overlaps else QCStatus.PASS
        check = QCCheck("subtitle.no_overlapping_cues", "subtitle", status, ["subtitle.overlapping_cues"])
        if overlaps:
            f = QCFinding("SUBTITLE_OVERLAPPING_CUES", FindingSeverity.FAIL, f"{len(overlaps)} pair(s) of cues overlap", evidence={"pairs": overlaps}, measurement_ids=["subtitle.overlapping_cues"])
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    # --- policy checks ---
    if rule.max_line_length is not None:
        excessive = _val(measurements, "subtitle.excessive_line_length") or []
        status = QCStatus.WARN if excessive else QCStatus.PASS
        check = QCCheck("subtitle.line_length_within_limit", "subtitle", status, ["subtitle.excessive_line_length"])
        if excessive:
            f = QCFinding("SUBTITLE_LINE_TOO_LONG", FindingSeverity.WARN, f"{len(excessive)} cue(s) exceed the max line length", evidence={"cues": excessive}, measurement_ids=["subtitle.excessive_line_length"])
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.max_cue_duration_sec is not None:
        excessive = _val(measurements, "subtitle.excessive_cue_duration") or []
        status = QCStatus.WARN if excessive else QCStatus.PASS
        check = QCCheck("subtitle.cue_duration_within_limit", "subtitle", status, ["subtitle.excessive_cue_duration"])
        if excessive:
            f = QCFinding("SUBTITLE_CUE_TOO_LONG", FindingSeverity.WARN, f"{len(excessive)} cue(s) exceed the max cue duration", evidence={"cues": excessive}, measurement_ids=["subtitle.excessive_cue_duration"])
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.max_gap_sec is not None:
        gaps = _val(measurements, "subtitle.gaps") or []
        offenders = [g for g in gaps if g["duration"] > rule.max_gap_sec]
        status = QCStatus.WARN if offenders else QCStatus.PASS
        check = QCCheck("subtitle.gaps_within_limit", "subtitle", status, ["subtitle.gaps"])
        if offenders:
            f = QCFinding("SUBTITLE_GAP_EXCEEDED", FindingSeverity.WARN, f"{len(offenders)} gap(s) exceed {rule.max_gap_sec}s with no active cue", evidence={"gaps": offenders}, measurement_ids=["subtitle.gaps"])
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.max_duration_delta_sec is not None:
        delta = _val(measurements, "subtitle.duration_delta_sec")
        if delta is None:
            checks.append(_unknown_check("subtitle.duration_matches_video", "subtitle", "video duration was not supplied for comparison", ["subtitle.duration_delta_sec"]))
        else:
            status = QCStatus.PASS if abs(delta) <= rule.max_duration_delta_sec else QCStatus.FAIL
            check = QCCheck("subtitle.duration_matches_video", "subtitle", status, ["subtitle.duration_delta_sec"])
            if status == QCStatus.FAIL:
                f = QCFinding(
                    "SUBTITLE_DURATION_MISMATCH", FindingSeverity.FAIL,
                    f"subtitle/video duration delta {delta}s exceeds tolerance {rule.max_duration_delta_sec}s",
                    evidence={"delta_sec": delta, "tolerance_sec": rule.max_duration_delta_sec}, measurement_ids=["subtitle.duration_delta_sec"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    if rule.min_coverage_ratio is not None:
        coverage = _val(measurements, "subtitle.coverage_ratio")
        if coverage is None:
            checks.append(_unknown_check("subtitle.coverage_within_limit", "subtitle", "video duration was not supplied for comparison", ["subtitle.coverage_ratio"]))
        else:
            status = QCStatus.PASS if coverage >= rule.min_coverage_ratio else QCStatus.WARN
            check = QCCheck("subtitle.coverage_within_limit", "subtitle", status, ["subtitle.coverage_ratio"])
            if status == QCStatus.WARN:
                f = QCFinding(
                    "SUBTITLE_COVERAGE_LOW", FindingSeverity.WARN,
                    f"subtitle coverage {coverage} is below the minimum {rule.min_coverage_ratio}",
                    evidence={"actual": coverage, "min_required": rule.min_coverage_ratio}, measurement_ids=["subtitle.coverage_ratio"],
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

    if rule.timeline_integrity is not None:
        check, finding = _evaluate_timeline_integrity(measurements, rule.timeline_integrity)
        checks.append(check)
        if finding is not None:
            findings.append(finding)

    _evaluate_cue_text_and_speech(checks, findings, measurements, rule)

    return checks, findings


# --- cue text and subtitle-vs-speech checks (ADR-023) ----------------------
#
# Every check below reads only measurements: ``subtitle.cue_texts`` (the
# visible text of each cue) and, for the speech checks,
# ``subtitle.transcript_words`` (the caller's word-timed transcript, read
# by measurements/transcript.py). Each violation carries ``time_sec`` (the
# moment to jump to), the cue's ``cue_index``/``cue_id``/``text`` and a
# one-line ``description``, sorted by time.

_SPEECH_MEASUREMENT_IDS = ["subtitle.cue_texts", "subtitle.transcript_words"]


def match_key(text: str) -> str:
    """The form cue text and transcript words are compared in: NFKC,
    case-folded, with every punctuation, symbol, separator (space) and
    control character removed."""

    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if unicodedata.category(ch)[0] not in "PSZC")


def _r(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 3)


def _cue_ref(cue: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "cue_index": cue["index"], "cue_id": cue.get("cue_id"),
        "start": _r(cue.get("start")), "end": _r(cue.get("end")),
        "text": "\n".join(cue.get("lines") or []),
    }


def _cue_label(cue: Dict[str, Any]) -> str:
    ident = f" (id {cue['cue_id']})" if cue.get("cue_id") is not None else ""
    text = " / ".join(cue.get("lines") or [])
    return f"cue #{cue['index']}{ident} \"{text}\""


def _word_ref(word: Dict[str, Any]) -> Dict[str, Any]:
    ref = {
        "position": word["position"], "index": word.get("index"), "text": word["text"],
        "start": _r(word["start"]), "end": _r(word["end"]),
    }
    if word.get("pos") is not None:
        ref["pos"] = word["pos"]
    if word.get("interpolated"):
        ref["interpolated"] = True
    return ref


def _at(seconds: Optional[float]) -> str:
    return "?" if seconds is None else f"{seconds:.3f}s"


def _sorted_by_time(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda v: (v["time_sec"] is None, v["time_sec"] or 0.0, v.get("cue_index") or 0))


def _record(
    checks: List[QCCheck],
    findings: List[QCFinding],
    *,
    check_id: str,
    code: str,
    violation_status: QCStatus,
    violations: List[Dict[str, Any]],
    evidence: Dict[str, Any],
    message: str,
    measurement_ids: List[str],
    unknown_reason: Optional[str] = None,
) -> None:
    """FAIL/WARN (``violation_status``) on any confirmed violation, else
    UNKNOWN when ``unknown_reason`` says part of the check could not be
    verified, else PASS."""

    violations = _sorted_by_time(violations)
    evidence = {**evidence, "violation_count": len(violations), "violations": violations}
    if violations:
        severity = FindingSeverity.FAIL if violation_status == QCStatus.FAIL else FindingSeverity.WARN
        finding = QCFinding(
            code, severity, message, evidence=evidence,
            timestamp=violations[0]["time_sec"], measurement_ids=list(measurement_ids),
        )
        findings.append(finding)
        checks.append(QCCheck(check_id, "subtitle", violation_status, list(measurement_ids), evidence=evidence, finding_codes=[code]))
    elif unknown_reason is not None:
        checks.append(QCCheck(check_id, "subtitle", QCStatus.UNKNOWN, list(measurement_ids), evidence=evidence, reason=unknown_reason))
    else:
        checks.append(QCCheck(check_id, "subtitle", QCStatus.PASS, list(measurement_ids), evidence=evidence))


def _timed_cues(cue_texts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        (c for c in cue_texts if c.get("start") is not None and c.get("end") is not None and c["start"] <= c["end"]),
        key=lambda c: (c["start"], c["index"]),
    )


def assign_words_to_cues(cues: List[Dict[str, Any]], words: List[Dict[str, Any]]) -> Dict[int, List[Dict[str, Any]]]:
    """Each word belongs to the cue on screen at the word's midpoint
    (``start <= mid < end``; with overlapping cues, the latest-starting
    one). Words in no cue belong to none. Per cue, words keep transcript
    order."""

    starts = [c["start"] for c in cues]
    # latest end among cues[0..j]: once it is <= mid, no earlier cue can contain mid
    reach: List[float] = []
    for cue in cues:
        reach.append(max(reach[-1], cue["end"]) if reach else cue["end"])
    out: Dict[int, List[Dict[str, Any]]] = {c["index"]: [] for c in cues}
    for word in words:
        mid = (word["start"] + word["end"]) / 2.0
        j = bisect_right(starts, mid) - 1
        while j >= 0 and reach[j] > mid:
            cue = cues[j]
            if cue["start"] <= mid < cue["end"]:
                out[cue["index"]].append(word)
                break
            j -= 1
    for cue_words in out.values():
        cue_words.sort(key=lambda w: w["position"])
    return out


def _transcript_words(measurements: MeasurementMap) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    m = measurements.get("subtitle.transcript_words")
    if m is None:
        return None, "no transcript was supplied (rules.subtitle.transcript)"
    if m.value is None:
        return None, m.notes or "the transcript could not be read"
    return m.value, None


def _check_word_boundaries(cues, words, tolerance: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ordered = sorted(words, key=lambda w: (w["start"], w["position"]))
    starts = [w["start"] for w in ordered]
    longest = max((w["end"] - w["start"] for w in ordered), default=0.0)
    confirmed, unconfirmed = [], []
    for cue in cues:
        for boundary in ("start", "end"):
            t = cue[boundary]
            for word in ordered[bisect_left(starts, t - longest):bisect_right(starts, t)]:
                if word["start"] + tolerance < t < word["end"] - tolerance:
                    inside = min(t - word["start"], word["end"] - t)
                    entry = {
                        "time_sec": _r(t), "boundary": boundary, **_cue_ref(cue),
                        "word": _word_ref(word), "inside_word_by_sec": _r(inside),
                        "description": (
                            f"{_at(t)} {_cue_label(cue)} {boundary}s {inside:.3f}s inside the word "
                            f"\"{word['text']}\" ({word['start']:.3f}-{word['end']:.3f}s)"
                        ),
                    }
                    (unconfirmed if word.get("interpolated") else confirmed).append(entry)
    return confirmed, unconfirmed


def _check_cue_lead(cues, by_cue, max_lead: float) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int]:
    confirmed, unconfirmed = [], []
    without_speech = 0
    for cue in cues:
        cue_words = by_cue.get(cue["index"]) or []
        if not cue_words:
            without_speech += 1
            continue
        first = min(cue_words, key=lambda w: (w["start"], w["position"]))
        lead = first["start"] - cue["start"]
        if lead > max_lead:
            entry = {
                "time_sec": _r(cue["start"]), **_cue_ref(cue), "first_word": _word_ref(first), "lead_sec": _r(lead),
                "description": (
                    f"{_at(cue['start'])} {_cue_label(cue)} appears {lead:.3f}s before its first word "
                    f"\"{first['text']}\" starts at {first['start']:.3f}s"
                ),
            }
            (unconfirmed if first.get("interpolated") else confirmed).append(entry)
    return confirmed, unconfirmed, without_speech


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    merged: List[Tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _check_uncovered_speech(cues, words, extra: List[CoveredInterval], max_uncovered: float):
    covered = _merge_intervals(
        [(c["start"], c["end"]) for c in cues if c["end"] > c["start"]]
        + [(iv.start, iv.end) for iv in extra if iv.end > iv.start]
    )
    bounds = [float("-inf")] + [x for iv in covered for x in iv] + [float("inf")]
    gaps = list(zip(bounds[0::2], bounds[1::2]))
    ordered = sorted(words, key=lambda w: (w["start"], w["position"]))
    starts = [w["start"] for w in ordered]
    longest = max((w["end"] - w["start"] for w in ordered), default=0.0)
    cue_ends = sorted(cues, key=lambda c: (c["end"], c["index"]))
    end_times = [c["end"] for c in cue_ends]
    cue_starts = [c["start"] for c in cues]
    confirmed, unconfirmed = [], []
    for g0, g1 in gaps:
        lo = 0 if g0 == float("-inf") else bisect_left(starts, g0 - longest)
        hi = len(ordered) if g1 == float("inf") else bisect_left(starts, g1)
        inside = [w for w in ordered[lo:hi] if w["end"] > g0 and w["start"] < g1 and w["end"] > w["start"]]
        if not inside:
            continue
        run_start = max(g0, min(w["start"] for w in inside))
        run_end = min(g1, max(w["end"] for w in inside))
        duration = run_end - run_start
        if duration <= max_uncovered:
            continue
        p = bisect_right(end_times, run_start) - 1
        n = bisect_left(cue_starts, run_end)
        previous_cue = _cue_ref(cue_ends[p]) if p >= 0 else None
        next_cue = _cue_ref(cues[n]) if n < len(cues) else None
        spoken = " ".join(w["text"] for w in inside)
        entry = {
            "time_sec": _r(run_start), "start": _r(run_start), "end": _r(run_end), "duration_sec": _r(duration),
            "spoken_text": spoken, "word_count": len(inside),
            "first_word": _word_ref(inside[0]), "last_word": _word_ref(inside[-1]),
            "previous_cue": previous_cue, "next_cue": next_cue,
            "description": f"{_at(run_start)}-{_at(run_end)} {duration:.3f}s of speech with no cue on screen: \"{spoken}\"",
        }
        (unconfirmed if any(w.get("interpolated") for w in inside) else confirmed).append(entry)
    return confirmed, unconfirmed


def _align_cue(cue: Dict[str, Any], cue_words: List[Dict[str, Any]]):
    """Character offsets of the cue's transcript words inside the cue's
    own text (both in ``match_key`` form), or None when the words do not
    spell the cue text exactly."""

    cue_key = "".join(match_key(line) for line in cue.get("lines") or [])
    if not cue_key or not cue_words:
        return None
    word_at: Dict[int, Dict[str, Any]] = {}
    ends = set()
    offset = 0
    for word in cue_words:
        key = match_key(word["text"])
        if not key:
            continue
        word_at.setdefault(offset, word)
        offset += len(key)
        ends.add(offset)
    if "".join(match_key(w["text"]) for w in cue_words) != cue_key:
        return None
    return word_at, ends


def _check_line_starts(cues, by_cue, words_available: bool, pos_set, phrases):
    phrase_keys = sorted(
        {(match_key(p), p) for p in phrases if match_key(p)}, key=lambda kp: (-len(kp[0]), kp[0]),
    )
    violations, unverified = [], []
    for cue in cues:
        alignment = _align_cue(cue, by_cue.get(cue["index"]) or []) if words_available else None
        offset = 0
        for number, line in enumerate(cue.get("lines") or [], start=1):
            key = match_key(line)
            line_offset = offset
            offset += len(key)
            if not key:
                continue
            first_word = alignment[0].get(line_offset) if alignment is not None else None
            phrase_hit = None
            for phrase_key, phrase in phrase_keys:
                if key.startswith(phrase_key) and (alignment is None or line_offset + len(phrase_key) in alignment[1]):
                    phrase_hit = phrase
                    break
            pos_hit = None
            if pos_set:
                if alignment is None:
                    unverified.append({
                        "time_sec": _r(cue["start"]), **_cue_ref(cue), "line_number": number, "line": line,
                        "reason": (
                            "the transcript words in this cue do not spell the cue text" if by_cue.get(cue["index"])
                            else "no transcript word falls inside this cue"
                        ),
                    })
                elif first_word is None:
                    unverified.append({
                        "time_sec": _r(cue["start"]), **_cue_ref(cue), "line_number": number, "line": line,
                        "reason": "the line starts inside a transcript word",
                    })
                elif first_word.get("pos") is None:
                    unverified.append({
                        "time_sec": _r(cue["start"]), **_cue_ref(cue), "line_number": number, "line": line,
                        "reason": f"the word \"{first_word['text']}\" has no pos label",
                    })
                elif first_word["pos"] in pos_set:
                    pos_hit = first_word["pos"]
            if phrase_hit is None and pos_hit is None:
                continue
            reasons = []
            if phrase_hit is not None:
                reasons.append(f"phrase \"{phrase_hit}\"")
            if pos_hit is not None:
                reasons.append(f"{pos_hit} \"{first_word['text']}\"")
            entry = {
                "time_sec": _r(cue["start"]), **_cue_ref(cue), "line_number": number, "line": line,
                "phrase": phrase_hit, "pos": pos_hit,
                "phrase_matched_on": None if phrase_hit is None else ("word_boundary" if alignment is not None else "text_prefix"),
                "description": f"{_at(cue['start'])} {_cue_label(cue)} line {number} starts with {' and '.join(reasons)}",
            }
            if first_word is not None:
                entry["word"] = _word_ref(first_word)
            violations.append(entry)
    return violations, unverified


def _evaluate_cue_text_and_speech(
    checks: List[QCCheck], findings: List[QCFinding], measurements: MeasurementMap, rule: SubtitleRule,
) -> None:
    wants_boundaries = rule.word_boundary_tolerance_sec is not None
    wants_lead = rule.max_cue_lead_sec is not None
    wants_coverage = rule.max_uncovered_speech_sec is not None
    wants_line_starts = rule.forbidden_line_start_pos is not None or rule.forbidden_line_start_phrases is not None
    if not any((wants_boundaries, wants_lead, wants_coverage, wants_line_starts,
                rule.max_total_chars is not None, rule.min_cue_chars is not None)):
        return

    cue_texts = _val(measurements, "subtitle.cue_texts")
    if cue_texts is None:
        reason = "subtitle.cue_texts was not measured"
        for wanted, check_id in (
            (wants_boundaries, "subtitle.cue_boundaries_on_word_boundaries"),
            (wants_lead, "subtitle.cue_not_early"),
            (wants_coverage, "subtitle.speech_covered"),
            (wants_line_starts, "subtitle.no_forbidden_line_start"),
            (rule.max_total_chars is not None, "subtitle.total_chars_within_limit"),
            (rule.min_cue_chars is not None, "subtitle.cue_chars_meet_minimum"),
        ):
            if wanted:
                checks.append(_unknown_check(check_id, "subtitle", reason, ["subtitle.cue_texts"]))
        return

    cues = _timed_cues(cue_texts)
    words, words_reason = _transcript_words(measurements)
    by_cue = assign_words_to_cues(cues, words) if words is not None else {}
    base = {"cues_checked": len(cues), "words_checked": len(words) if words is not None else None}

    if wants_boundaries:
        tolerance = rule.word_boundary_tolerance_sec
        if words is None:
            checks.append(QCCheck("subtitle.cue_boundaries_on_word_boundaries", "subtitle", QCStatus.UNKNOWN, list(_SPEECH_MEASUREMENT_IDS), reason=words_reason))
        else:
            confirmed, unconfirmed = _check_word_boundaries(cues, words, tolerance)
            _record(
                checks, findings,
                check_id="subtitle.cue_boundaries_on_word_boundaries", code="SUBTITLE_CUE_BOUNDARY_INSIDE_WORD",
                violation_status=QCStatus.FAIL, violations=confirmed,
                evidence={**base, "tolerance_sec": tolerance, "unconfirmed": _sorted_by_time(unconfirmed)},
                message=f"{len(confirmed)} cue boundary(ies) fall more than {tolerance}s inside a spoken word",
                measurement_ids=_SPEECH_MEASUREMENT_IDS,
                unknown_reason=(f"{len(unconfirmed)} boundary(ies) fall inside a word whose timing is interpolated" if unconfirmed else None),
            )

    if wants_lead:
        max_lead = rule.max_cue_lead_sec
        if words is None:
            checks.append(QCCheck("subtitle.cue_not_early", "subtitle", QCStatus.UNKNOWN, list(_SPEECH_MEASUREMENT_IDS), reason=words_reason))
        else:
            confirmed, unconfirmed, without_speech = _check_cue_lead(cues, by_cue, max_lead)
            _record(
                checks, findings,
                check_id="subtitle.cue_not_early", code="SUBTITLE_CUE_EARLY",
                violation_status=QCStatus.FAIL, violations=confirmed,
                evidence={**base, "max_cue_lead_sec": max_lead, "cues_without_speech": without_speech,
                          "unconfirmed": _sorted_by_time(unconfirmed)},
                message=f"{len(confirmed)} cue(s) appear more than {max_lead}s before their first spoken word",
                measurement_ids=_SPEECH_MEASUREMENT_IDS,
                unknown_reason=(f"{len(unconfirmed)} cue(s) lead a word whose timing is interpolated" if unconfirmed else None),
            )

    if wants_coverage:
        max_uncovered = rule.max_uncovered_speech_sec
        if words is None:
            checks.append(QCCheck("subtitle.speech_covered", "subtitle", QCStatus.UNKNOWN, list(_SPEECH_MEASUREMENT_IDS), reason=words_reason))
        else:
            extra = rule.extra_covered_intervals or []
            confirmed, unconfirmed = _check_uncovered_speech(cues, words, extra, max_uncovered)
            _record(
                checks, findings,
                check_id="subtitle.speech_covered", code="SUBTITLE_SPEECH_UNCOVERED",
                violation_status=QCStatus.FAIL, violations=confirmed,
                evidence={**base, "max_uncovered_speech_sec": max_uncovered,
                          "extra_covered_intervals": [{"start": iv.start, "end": iv.end, "label": iv.label} for iv in extra],
                          "unconfirmed": _sorted_by_time(unconfirmed)},
                message=f"{len(confirmed)} stretch(es) of speech longer than {max_uncovered}s have no cue on screen",
                measurement_ids=_SPEECH_MEASUREMENT_IDS,
                unknown_reason=(f"{len(unconfirmed)} uncovered stretch(es) include words whose timing is interpolated" if unconfirmed else None),
            )

    if wants_line_starts:
        pos_set = sorted(set(rule.forbidden_line_start_pos or []))
        phrases = list(rule.forbidden_line_start_phrases or [])
        pos_reason = None
        words_usable = words is not None
        if pos_set:
            if words is None:
                pos_reason = f"the pos part needs a transcript: {words_reason}"
            elif not any(w.get("pos") is not None for w in words):
                pos_reason = "the transcript carries no pos labels, so the pos part was not checked"
                words_usable = True
        violations, unverified = _check_line_starts(
            cues, by_cue, words_usable, set(pos_set) if pos_reason is None else set(), phrases,
        )
        if pos_reason is None and unverified:
            pos_reason = f"the pos of {len(unverified)} line start(s) could not be determined"
        pos_part = {"status": "NOT_REQUESTED"} if not pos_set else (
            {"status": "UNKNOWN", "reason": pos_reason} if pos_reason is not None else {"status": "CHECKED"}
        )
        _record(
            checks, findings,
            check_id="subtitle.no_forbidden_line_start", code="SUBTITLE_FORBIDDEN_LINE_START",
            violation_status=QCStatus.FAIL, violations=violations,
            evidence={**base, "scope": "each line of each cue", "forbidden_pos": pos_set, "forbidden_phrases": phrases,
                      "pos_part": pos_part, "unverified": _sorted_by_time(unverified)},
            message=f"{len(violations)} subtitle line(s) start with a forbidden word or phrase",
            measurement_ids=_SPEECH_MEASUREMENT_IDS if words is not None else ["subtitle.cue_texts"],
            unknown_reason=pos_reason,
        )

    if rule.max_total_chars is not None:
        limit = rule.max_total_chars
        violations = [
            {"time_sec": _r(c.get("start")), **_cue_ref(c), "chars": c["chars"], "max_total_chars": limit,
             "description": f"{_at(c.get('start'))} {_cue_label(c)} has {c['chars']} characters (limit {limit})"}
            for c in cue_texts if c["chars"] > limit
        ]
        _record(
            checks, findings,
            check_id="subtitle.total_chars_within_limit", code="SUBTITLE_CUE_TOO_MANY_CHARS",
            violation_status=QCStatus.FAIL, violations=violations,
            evidence={"cues_checked": len(cue_texts), "max_total_chars": limit, "counting": "NFKC code points, line breaks not counted"},
            message=f"{len(violations)} cue(s) have more than {limit} characters",
            measurement_ids=["subtitle.cue_texts"],
        )

    if rule.min_cue_chars is not None:
        minimum = rule.min_cue_chars
        violations = [
            {"time_sec": _r(c.get("start")), **_cue_ref(c), "chars": c["chars"], "min_cue_chars": minimum,
             "description": f"{_at(c.get('start'))} {_cue_label(c)} has only {c['chars']} character(s) (minimum {minimum})"}
            for c in cue_texts if 0 < c["chars"] < minimum
        ]
        _record(
            checks, findings,
            check_id="subtitle.cue_chars_meet_minimum", code="SUBTITLE_CUE_TOO_FEW_CHARS",
            violation_status=QCStatus.WARN, violations=violations,
            evidence={"cues_checked": len(cue_texts), "min_cue_chars": minimum, "counting": "NFKC code points, line breaks not counted",
                      "empty_cues_skipped": "empty cues are reported by subtitle.no_empty_cues"},
            message=f"{len(violations)} cue(s) have fewer than {minimum} characters",
            measurement_ids=["subtitle.cue_texts"],
        )


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@dataclass
class DeliveryRule:
    expected_extension: Optional[str] = None
    expected_container: Optional[str] = None
    min_size_bytes: Optional[int] = None
    max_size_bytes: Optional[int] = None
    require_video: Optional[bool] = None
    require_audio: Optional[bool] = None
    require_subtitle: Optional[bool] = None
    video: Optional[VideoRule] = None
    audio: Optional[AudioRule] = None
    subtitle: Optional[SubtitleRule] = None


def evaluate_delivery_basics(measurements: MeasurementMap, rule: DeliveryRule) -> Tuple[List[QCCheck], List[QCFinding]]:
    checks: List[QCCheck] = []
    findings: List[QCFinding] = []

    if rule.min_size_bytes is not None or rule.max_size_bytes is not None:
        size = _val(measurements, "container.size_bytes")
        too_small = rule.min_size_bytes is not None and not (size is not None and size >= rule.min_size_bytes)
        too_large = rule.max_size_bytes is not None and not (size is not None and size <= rule.max_size_bytes)
        status = QCStatus.FAIL if (too_small or too_large) else QCStatus.PASS
        check = QCCheck("delivery.file_size_within_limit", "delivery", status, ["container.size_bytes"])
        if too_small:
            f = QCFinding(
                "DELIVERY_FILE_TOO_SMALL", FindingSeverity.FAIL,
                f"file size {size} bytes is below the minimum {rule.min_size_bytes} bytes",
                evidence={"actual": size, "min_required": rule.min_size_bytes}, measurement_ids=["container.size_bytes"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        if too_large:
            f = QCFinding(
                "DELIVERY_FILE_TOO_LARGE", FindingSeverity.FAIL,
                f"file size {size} bytes exceeds the maximum {rule.max_size_bytes} bytes",
                evidence={"actual": size, "max_allowed": rule.max_size_bytes}, measurement_ids=["container.size_bytes"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.expected_extension is not None:
        actual_ext = _val(measurements, "delivery.extension")
        status = QCStatus.PASS if actual_ext == rule.expected_extension.lower() else QCStatus.FAIL
        check = QCCheck("delivery.extension_matches_expected", "delivery", status, ["delivery.extension"])
        if status == QCStatus.FAIL:
            f = QCFinding(
                "DELIVERY_EXTENSION_MISMATCH", FindingSeverity.FAIL,
                f"extension {actual_ext!r} does not match expected {rule.expected_extension!r}",
                evidence={"actual": actual_ext, "expected": rule.expected_extension}, measurement_ids=["delivery.extension"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    if rule.expected_container is not None:
        fmt = _val(measurements, "container.format_name") or ""
        ok = rule.expected_container.lower() in [p.lower() for p in fmt.split(",")]
        status = QCStatus.PASS if ok else QCStatus.FAIL
        check = QCCheck("delivery.container_matches_expected", "delivery", status, ["container.format_name"])
        if not ok:
            f = QCFinding(
                "DELIVERY_CONTAINER_MISMATCH", FindingSeverity.FAIL,
                f"container {fmt!r} does not match expected {rule.expected_container!r}",
                evidence={"actual": fmt, "expected": rule.expected_container}, measurement_ids=["container.format_name"],
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    return checks, findings


# ---------------------------------------------------------------------------
# Delivery package (N named artifacts validated together as one delivery -
# see ADR-010: a new kind, deliberately not an extension of DeliveryRule)
# ---------------------------------------------------------------------------


@dataclass
class DeliveryArtifactRule:
    """The QC policy for one named artifact inside a delivery package.

    ``artifact_id`` must match an entry in the request's own ``artifacts``
    list - that list (not this rule) is what actually names the file
    paths, exactly like ``request.subtitle``/``request.reference_video``
    are separate from ``DeliveryRule`` today. ``artifact_type``, when
    given, is cross-checked against the request's declared type for the
    same id (a caller-side sanity check, not a security boundary).
    """

    artifact_id: str
    artifact_type: Optional[str] = None
    required: bool = True
    expected_extension: Optional[str] = None
    min_size_bytes: Optional[int] = None
    video: Optional[VideoRule] = None
    audio: Optional[AudioRule] = None
    subtitle: Optional[SubtitleRule] = None


@dataclass
class ArtifactDurationConsistencyRule:
    """N artifacts (by id, e.g. the main video and its subtitle) whose
    measured durations must agree within a caller-chosen tolerance. Reads
    ``container.duration_sec`` (video/audio) or ``subtitle.duration_sec``
    (subtitle) from each named artifact's own already-gathered
    measurements - never re-reads a file or compares anything semantic.
    """

    artifact_ids: List[str]
    max_delta_sec: float


@dataclass
class ArtifactDependencyRule:
    """If ``artifact_id`` is present, ``requires_artifact_id`` must be too."""

    artifact_id: str
    requires_artifact_id: str


@dataclass
class CrossArtifactRule:
    duration_consistency: List[ArtifactDurationConsistencyRule] = field(default_factory=list)
    dependencies: List[ArtifactDependencyRule] = field(default_factory=list)


@dataclass
class DeliveryPackageRule:
    artifacts: List[DeliveryArtifactRule] = field(default_factory=list)
    cross_artifact: Optional[CrossArtifactRule] = None


def _artifact_duration_sec(amap: MeasurementMap) -> Optional[float]:
    for measurement_id in ("container.duration_sec", "subtitle.duration_sec"):
        value = _val(amap, measurement_id)
        if value is not None:
            return value
    return None


def _evaluate_cross_artifact(
    rule: CrossArtifactRule, per_artifact_measurements: Dict[str, MeasurementMap]
) -> Tuple[List[QCCheck], List[QCFinding]]:
    checks: List[QCCheck] = []
    findings: List[QCFinding] = []

    for dc in rule.duration_consistency:
        durations: Dict[str, float] = {}
        unresolved: List[str] = []
        for aid in dc.artifact_ids:
            duration = _artifact_duration_sec(per_artifact_measurements.get(aid, {}))
            if duration is None:
                unresolved.append(aid)
            else:
                durations[aid] = duration

        if unresolved:
            checks.append(
                _unknown_check(
                    "delivery_package.duration_consistent", "delivery_package",
                    f"duration could not be measured for: {unresolved}", [],
                )
            )
            continue

        spread = max(durations.values()) - min(durations.values())
        status = QCStatus.PASS if spread <= dc.max_delta_sec else QCStatus.FAIL
        check = QCCheck(
            "delivery_package.duration_consistent", "delivery_package", status, [],
            evidence={"durations": durations, "spread_sec": spread, "max_delta_sec": dc.max_delta_sec},
        )
        if status == QCStatus.FAIL:
            f = QCFinding(
                "DELIVERY_PACKAGE_DURATION_MISMATCH", FindingSeverity.FAIL,
                f"artifact durations differ by {spread}s (max allowed {dc.max_delta_sec}s): {durations}",
                evidence={"durations": durations, "spread_sec": spread, "max_delta_sec": dc.max_delta_sec},
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    for dep in rule.dependencies:
        present = bool(_val(per_artifact_measurements.get(dep.artifact_id, {}), "delivery_package.artifact_present"))
        if not present:
            continue  # the dependent artifact isn't here at all; nothing to enforce

        dep_present = bool(
            _val(per_artifact_measurements.get(dep.requires_artifact_id, {}), "delivery_package.artifact_present")
        )
        status = QCStatus.PASS if dep_present else QCStatus.FAIL
        check = QCCheck(
            "delivery_package.dependency_satisfied", "delivery_package", status, [],
            evidence={"artifact_id": dep.artifact_id, "requires_artifact_id": dep.requires_artifact_id},
        )
        if status == QCStatus.FAIL:
            f = QCFinding(
                "DELIVERY_PACKAGE_DEPENDENCY_MISSING", FindingSeverity.FAIL,
                f"artifact {dep.artifact_id!r} is present but its required companion {dep.requires_artifact_id!r} is not",
                evidence={"artifact_id": dep.artifact_id, "requires_artifact_id": dep.requires_artifact_id},
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

    return checks, findings


def evaluate_delivery_package(
    rule: DeliveryPackageRule, per_artifact_measurements: Dict[str, MeasurementMap]
) -> Tuple[List[QCCheck], List[QCFinding]]:
    """Structural, per-artifact checks only (STEP: Phase 1 scope).

    Deliberately does not compare artifacts against each other - that is
    cross-artifact validation (Phase 2, ``feature/cross-artifact-qc``),
    a distinct, not-yet-built capability. This only asks, for each
    artifact this rule names: is it present (when required), the right
    size/extension, and - if a nested video/audio/subtitle rule was
    given - does *that one artifact* pass those checks on its own.
    """

    checks: List[QCCheck] = []
    findings: List[QCFinding] = []

    for artifact_rule in rule.artifacts:
        aid = artifact_rule.artifact_id
        amap = per_artifact_measurements.get(aid, {})
        present = bool(_val(amap, "delivery_package.artifact_present"))

        status = QCStatus.FAIL if (artifact_rule.required and not present) else QCStatus.PASS
        check = QCCheck(
            "delivery_package.artifact_present", "delivery_package", status,
            ["delivery_package.artifact_present"], artifact_id=aid,
        )
        if status == QCStatus.FAIL:
            f = QCFinding(
                "DELIVERY_PACKAGE_ARTIFACT_MISSING", FindingSeverity.FAIL,
                f"required artifact {aid!r} is not present in the delivery package",
                measurement_ids=["delivery_package.artifact_present"], artifact_id=aid,
            )
            findings.append(f)
            check.finding_codes.append(f.code)
        checks.append(check)

        if not present:
            continue  # nothing else can be evaluated for an absent artifact

        if artifact_rule.min_size_bytes is not None:
            size = _val(amap, "delivery_package.artifact_size_bytes")
            status = QCStatus.PASS if (size is not None and size >= artifact_rule.min_size_bytes) else QCStatus.FAIL
            check = QCCheck(
                "delivery_package.artifact_size_within_limit", "delivery_package", status,
                ["delivery_package.artifact_size_bytes"], artifact_id=aid,
            )
            if status == QCStatus.FAIL:
                f = QCFinding(
                    "DELIVERY_PACKAGE_ARTIFACT_TOO_SMALL", FindingSeverity.FAIL,
                    f"artifact {aid!r} size {size} bytes is below the minimum {artifact_rule.min_size_bytes} bytes",
                    evidence={"actual": size, "min_required": artifact_rule.min_size_bytes},
                    measurement_ids=["delivery_package.artifact_size_bytes"], artifact_id=aid,
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

        if artifact_rule.expected_extension is not None:
            actual_ext = _val(amap, "delivery_package.artifact_extension")
            status = QCStatus.PASS if actual_ext == artifact_rule.expected_extension.lower() else QCStatus.FAIL
            check = QCCheck(
                "delivery_package.artifact_extension_matches_expected", "delivery_package", status,
                ["delivery_package.artifact_extension"], artifact_id=aid,
            )
            if status == QCStatus.FAIL:
                f = QCFinding(
                    "DELIVERY_PACKAGE_ARTIFACT_EXTENSION_MISMATCH", FindingSeverity.FAIL,
                    f"artifact {aid!r} extension {actual_ext!r} does not match expected {artifact_rule.expected_extension!r}",
                    evidence={"actual": actual_ext, "expected": artifact_rule.expected_extension},
                    measurement_ids=["delivery_package.artifact_extension"], artifact_id=aid,
                )
                findings.append(f)
                check.finding_codes.append(f.code)
            checks.append(check)

        if artifact_rule.video is not None:
            duration = _val(amap, "container.duration_sec")
            c, f = evaluate_video(amap, artifact_rule.video, duration)
            checks += [replace(x, artifact_id=aid) for x in c]
            findings += [replace(x, artifact_id=aid) for x in f]

        if artifact_rule.audio is not None:
            c, f = evaluate_audio(amap, artifact_rule.audio)
            checks += [replace(x, artifact_id=aid) for x in c]
            findings += [replace(x, artifact_id=aid) for x in f]

        if artifact_rule.subtitle is not None:
            c, f = evaluate_subtitle(amap, artifact_rule.subtitle)
            checks += [replace(x, artifact_id=aid) for x in c]
            findings += [replace(x, artifact_id=aid) for x in f]

    if rule.cross_artifact is not None:
        c, f = _evaluate_cross_artifact(rule.cross_artifact, per_artifact_measurements)
        checks += c
        findings += f

    return checks, findings
