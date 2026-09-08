"""Unit-level defense checks: a policy comparison must report UNKNOWN, not
FAIL, when the underlying measurement genuinely could not be obtained.

Real ffprobe always reports width/height/codec for a valid video stream,
so these specific gaps cannot be reproduced through the full pipeline
against real media (that would require a corrupt/unusual file, which is
its own can of worms) - they are exercised directly against the pure
rule-evaluation functions with a synthetic, incomplete measurement map,
which is exactly what those functions are guarding against.
"""

from qc_skill.models import QCMeasurement, QCStatus
from qc_skill.rules import VideoRule, evaluate_video


def _measurements(*measurements):
    return {m.id: m for m in measurements}


def test_resolution_check_is_unknown_when_width_unmeasured():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.width", "video", "width", None),
        QCMeasurement("video.height", "video", "height", 1080),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_width=1920, expected_height=1080), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.resolution_matches_expected"].status == QCStatus.UNKNOWN
    assert by_id["video.resolution_matches_expected"].reason
    assert not any(f.code == "VIDEO_RESOLUTION_MISMATCH" for f in findings)


def test_codec_check_is_unknown_when_codec_unmeasured():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.codec", "video", "codec", None),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_codec="h264"), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.codec_matches_expected"].status == QCStatus.UNKNOWN
    assert not any(f.code == "VIDEO_CODEC_MISMATCH" for f in findings)


def test_pixel_format_check_is_unknown_when_unmeasured():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.pixel_format", "video", "pixel_format", None),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_pixel_format="yuv420p"), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.pixel_format_matches_expected"].status == QCStatus.UNKNOWN
    assert not any(f.code == "VIDEO_PIXEL_FORMAT_MISMATCH" for f in findings)


def test_aspect_ratio_check_is_unknown_when_unmeasured():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.aspect_ratio", "video", "aspect_ratio", None),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_aspect_ratio="16:9"), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.aspect_ratio_matches_expected"].status == QCStatus.UNKNOWN
    assert not any(f.code == "VIDEO_ASPECT_MISMATCH" for f in findings)


def test_resolution_check_still_fails_on_a_genuine_mismatch():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.width", "video", "width", 1280),
        QCMeasurement("video.height", "video", "height", 720),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_width=1920, expected_height=1080), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.resolution_matches_expected"].status == QCStatus.FAIL
    assert any(f.code == "VIDEO_RESOLUTION_MISMATCH" for f in findings)


def test_resolution_meets_minimum_is_unknown_when_width_unmeasured():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.width", "video", "width", None),
        QCMeasurement("video.height", "video", "height", 1080),
    )
    checks, findings = evaluate_video(measurements, VideoRule(min_width=1920, min_height=1080), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.resolution_meets_minimum"].status == QCStatus.UNKNOWN
    assert by_id["video.resolution_meets_minimum"].reason
    assert not any(f.code == "VIDEO_RESOLUTION_BELOW_MINIMUM" for f in findings)


def test_resolution_meets_minimum_passes_when_above_the_floor():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.width", "video", "width", 3840),
        QCMeasurement("video.height", "video", "height", 2160),
    )
    checks, findings = evaluate_video(measurements, VideoRule(min_width=1920, min_height=1080), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.resolution_meets_minimum"].status == QCStatus.PASS
    assert not findings


def test_resolution_meets_minimum_fails_below_the_floor():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.width", "video", "width", 640),
        QCMeasurement("video.height", "video", "height", 480),
    )
    checks, findings = evaluate_video(measurements, VideoRule(min_width=1920, min_height=1080), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.resolution_meets_minimum"].status == QCStatus.FAIL
    assert any(f.code == "VIDEO_RESOLUTION_BELOW_MINIMUM" for f in findings)


def test_duration_ceiling_is_unknown_when_duration_unmeasured():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
    )
    checks, findings = evaluate_video(measurements, VideoRule(max_duration_sec=60.0), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.duration_within_limit"].status == QCStatus.UNKNOWN
    assert by_id["video.duration_within_limit"].reason
    assert not any(f.code == "VIDEO_DURATION_EXCEEDED" for f in findings)


def test_duration_ceiling_fails_when_over_the_limit():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
    )
    checks, findings = evaluate_video(measurements, VideoRule(max_duration_sec=60.0), 90.0)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.duration_within_limit"].status == QCStatus.FAIL
    assert any(f.code == "VIDEO_DURATION_EXCEEDED" for f in findings)


def test_duration_ceiling_passes_when_within_the_limit():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
    )
    checks, findings = evaluate_video(measurements, VideoRule(max_duration_sec=60.0), 30.0)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.duration_within_limit"].status == QCStatus.PASS
    assert not findings


def test_vfr_check_is_unknown_when_the_measurement_could_not_be_made():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.variable_frame_rate_suspected", "video", "variable_frame_rate_suspected", None),
    )
    checks, findings = evaluate_video(measurements, VideoRule(disallow_vfr=True), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.frame_rate_is_constant"].status == QCStatus.UNKNOWN
    assert by_id["video.frame_rate_is_constant"].reason
    assert not any(f.code == "VIDEO_VARIABLE_FRAME_RATE" for f in findings)


def test_vfr_check_fails_when_variable_frame_rate_is_suspected_and_disallowed():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.variable_frame_rate_suspected", "video", "variable_frame_rate_suspected", True),
    )
    checks, findings = evaluate_video(measurements, VideoRule(disallow_vfr=True), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.frame_rate_is_constant"].status == QCStatus.FAIL
    assert any(f.code == "VIDEO_VARIABLE_FRAME_RATE" for f in findings)


def test_vfr_check_passes_for_a_constant_frame_rate():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.variable_frame_rate_suspected", "video", "variable_frame_rate_suspected", False),
    )
    checks, findings = evaluate_video(measurements, VideoRule(disallow_vfr=True), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.frame_rate_is_constant"].status == QCStatus.PASS
    assert not findings


def test_vfr_check_is_not_produced_when_disallow_vfr_is_not_set():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.variable_frame_rate_suspected", "video", "variable_frame_rate_suspected", True),
    )
    checks, findings = evaluate_video(measurements, VideoRule(), None)
    by_id = {c.check_id: c for c in checks}
    assert "video.frame_rate_is_constant" not in by_id


def test_color_transfer_check_is_unknown_when_unmeasured():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.color_transfer", "video", "color_transfer", None),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_color_transfer="bt709"), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.color_transfer_matches_expected"].status == QCStatus.UNKNOWN
    assert not any(f.code == "VIDEO_COLOR_TRANSFER_MISMATCH" for f in findings)


def test_color_transfer_check_flags_hdr_delivered_to_an_sdr_only_expectation():
    # smpte2084 (PQ/HDR10) does not match an SDR-only expected_color_transfer.
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.color_transfer", "video", "color_transfer", "smpte2084"),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_color_transfer="bt709"), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.color_transfer_matches_expected"].status == QCStatus.FAIL
    assert any(f.code == "VIDEO_COLOR_TRANSFER_MISMATCH" for f in findings)


def test_color_transfer_check_passes_when_sdr_matches_expected():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.color_transfer", "video", "color_transfer", "bt709"),
    )
    checks, findings = evaluate_video(measurements, VideoRule(expected_color_transfer="bt709"), None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.color_transfer_matches_expected"].status == QCStatus.PASS
    assert not findings


def test_color_primaries_and_range_and_space_checks_follow_the_same_pattern():
    measurements = _measurements(
        QCMeasurement("video.stream_present", "video", "stream_present", True),
        QCMeasurement("video.color_range", "video", "color_range", "tv"),
        QCMeasurement("video.color_space", "video", "color_space", "bt2020nc"),
        QCMeasurement("video.color_primaries", "video", "color_primaries", "bt2020"),
    )
    rule = VideoRule(expected_color_range="pc", expected_color_space="bt709", expected_color_primaries="bt709")
    checks, findings = evaluate_video(measurements, rule, None)
    by_id = {c.check_id: c for c in checks}
    assert by_id["video.color_range_matches_expected"].status == QCStatus.FAIL
    assert by_id["video.color_space_matches_expected"].status == QCStatus.FAIL
    assert by_id["video.color_primaries_matches_expected"].status == QCStatus.FAIL
    codes = {f.code for f in findings}
    assert {"VIDEO_COLOR_RANGE_MISMATCH", "VIDEO_COLOR_SPACE_MISMATCH", "VIDEO_COLOR_PRIMARIES_MISMATCH"} <= codes
