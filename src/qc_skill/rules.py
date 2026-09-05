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

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Tuple

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


@dataclass
class VideoRule:
    expected_width: Optional[int] = None
    expected_height: Optional[int] = None
    expected_frame_rate: Optional[float] = None
    frame_rate_tolerance: float = 0.05
    expected_codec: Optional[str] = None
    expected_pixel_format: Optional[str] = None
    expected_aspect_ratio: Optional[str] = None
    max_single_black_sec: Optional[float] = None
    max_total_black_sec: Optional[float] = None
    max_single_freeze_sec: Optional[float] = None
    max_total_freeze_sec: Optional[float] = None
    max_decode_errors: int = 0  # baseline tolerance; 0 = any decode error fails


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

    return checks, findings


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------


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

    return checks, findings


# ---------------------------------------------------------------------------
# Subtitle
# ---------------------------------------------------------------------------


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


def evaluate_subtitle(measurements: MeasurementMap, rule: Optional[SubtitleRule]) -> Tuple[List[QCCheck], List[QCFinding]]:
    checks: List[QCCheck] = []
    findings: List[QCFinding] = []
    rule = rule or SubtitleRule()

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

    return checks, findings


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


@dataclass
class DeliveryRule:
    expected_extension: Optional[str] = None
    expected_container: Optional[str] = None
    min_size_bytes: Optional[int] = None
    require_video: Optional[bool] = None
    require_audio: Optional[bool] = None
    require_subtitle: Optional[bool] = None
    video: Optional[VideoRule] = None
    audio: Optional[AudioRule] = None
    subtitle: Optional[SubtitleRule] = None


def evaluate_delivery_basics(measurements: MeasurementMap, rule: DeliveryRule) -> Tuple[List[QCCheck], List[QCFinding]]:
    checks: List[QCCheck] = []
    findings: List[QCFinding] = []

    if rule.min_size_bytes is not None:
        size = _val(measurements, "container.size_bytes")
        status = QCStatus.PASS if (size is not None and size >= rule.min_size_bytes) else QCStatus.FAIL
        check = QCCheck("delivery.file_size_within_limit", "delivery", status, ["container.size_bytes"])
        if status == QCStatus.FAIL:
            f = QCFinding(
                "DELIVERY_FILE_TOO_SMALL", FindingSeverity.FAIL,
                f"file size {size} bytes is below the minimum {rule.min_size_bytes} bytes",
                evidence={"actual": size, "min_required": rule.min_size_bytes}, measurement_ids=["container.size_bytes"],
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
