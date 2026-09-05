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
