from qc_skill.measurements.video import measure_video_streams
from tests.helpers import checks_by_id, measurements_by_id, run


def _video_probe(*, r_frame_rate, avg_frame_rate):
    return {"streams": [{"codec_type": "video", "index": 0, "width": 320, "height": 240, "r_frame_rate": r_frame_rate, "avg_frame_rate": avg_frame_rate}]}


def test_variable_frame_rate_suspected_when_r_and_avg_rate_diverge():
    m = {x.id: x for x in measure_video_streams(_video_probe(r_frame_rate="30/1", avg_frame_rate="24/1"))}
    assert m["video.variable_frame_rate_suspected"].value is True


def test_variable_frame_rate_not_suspected_when_r_and_avg_rate_agree():
    m = {x.id: x for x in measure_video_streams(_video_probe(r_frame_rate="25/1", avg_frame_rate="25/1"))}
    assert m["video.variable_frame_rate_suspected"].value is False


def test_variable_frame_rate_suspected_is_none_when_a_rate_is_unavailable():
    m = {x.id: x for x in measure_video_streams(_video_probe(r_frame_rate="25/1", avg_frame_rate="0/0"))}
    assert m["video.variable_frame_rate_suspected"].value is None


def test_inspect_clean_video_has_no_checks(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    assert resp["status"] == "completed"
    assert resp["report"]["checks"] == []
    m = measurements_by_id(resp)
    assert m["video.stream_present"]["value"] is True
    assert m["video.width"]["value"] == 320
    assert m["video.height"]["value"] == 240
    assert m["video.codec"]["value"] == "h264"
    assert abs(m["video.frame_rate"]["value"] - 25.0) < 0.01
    assert m["video.frame_count"]["value"] == 100
    assert m["video.pixel_format"]["value"] == "yuv420p"
    assert m["video.black_segments"]["value"] == []
    assert m["video.freeze_segments"]["value"] == []
    assert m["video.decode_error_count"]["value"] == 0


def test_check_clean_video_matching_rule_passes(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"expected_width": 320, "expected_height": 240, "expected_frame_rate": 25}},
    }
    resp = run(doc, workspace)
    assert resp["report"]["overall_status"] == "PASS"
    checks = checks_by_id(resp)
    assert checks["video.resolution_matches_expected"]["status"] == "PASS"
    assert checks["video.frame_rate_matches_expected"]["status"] == "PASS"
    assert checks["video.decodes_without_errors"]["status"] == "PASS"


def test_check_clean_video_mismatched_resolution_fails(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"expected_width": 1920, "expected_height": 1080}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.resolution_matches_expected"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "VIDEO_RESOLUTION_MISMATCH" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_video_missing_stream_fails_baseline(media, workspace):
    # video_no_audio.mp4 does have a video stream; use kind=audio to exercise
    # the "stream missing" path against a video-only file instead.
    doc = {"operation": "check", "kind": "audio", "input": str(media["video_no_audio"]), "rules": {"audio": {"require_audio_stream": True}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.stream_present_matches_expected"]["status"] == "FAIL"
    assert resp["report"]["overall_status"] == "FAIL"


def test_black_frame_detected_as_measurement(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["black"])}
    resp = run(doc, workspace)
    segments = measurements_by_id(resp)["video.black_segments"]["value"]
    assert len(segments) == 1
    assert 1.0 < segments[0]["duration"] < 2.0


def test_black_frame_check_fails_when_over_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["black"]),
        "rules": {"video": {"max_single_black_sec": 0.2}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.black_frames_within_tolerance"]["status"] == "FAIL"


def test_black_frame_check_passes_when_within_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["black"]),
        "rules": {"video": {"max_single_black_sec": 5.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.black_frames_within_tolerance"]["status"] == "PASS"


def test_clean_video_has_no_black_frame_finding(media, workspace):
    doc = {"operation": "check", "kind": "video", "input": str(media["clean"]), "rules": {"video": {"max_single_black_sec": 0.1}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.black_frames_within_tolerance"]["status"] == "PASS"


def test_freeze_frame_detected_as_measurement(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["freeze"])}
    resp = run(doc, workspace)
    segments = measurements_by_id(resp)["video.freeze_segments"]["value"]
    assert len(segments) >= 1


def test_freeze_frame_check_fails_when_over_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["freeze"]),
        "rules": {"video": {"max_single_freeze_sec": 0.5}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.freeze_frames_within_tolerance"]["status"] == "FAIL"


def test_corrupted_video_fails_decode_integrity(media, workspace):
    doc = {"operation": "check", "kind": "video", "input": str(media["corrupted"])}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.decodes_without_errors"]["status"] == "FAIL"
    m = measurements_by_id(resp)
    assert m["video.decode_error_count"]["value"] > 0
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "VIDEO_DECODE_ERROR" in codes


def test_clean_video_passes_decode_integrity(media, workspace):
    doc = {"operation": "check", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.decodes_without_errors"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_unknown_status_is_not_conflated_with_pass_when_no_video_stream(media, workspace):
    # A pure-audio file evaluated under kind=video should FAIL the
    # stream-presence check, not silently pass because nothing else ran.
    doc = {"operation": "check", "kind": "video", "input": str(media["loud_clipping"])}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.stream_present"]["status"] == "FAIL"
    assert resp["report"]["overall_status"] == "FAIL"


def test_duration_ceiling_check_fails_when_exceeded(media, workspace):
    # clean.mp4 is 4s (see tests/fixtures/generate.py).
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"max_duration_sec": 2.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.duration_within_limit"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "VIDEO_DURATION_EXCEEDED" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_duration_ceiling_check_passes_when_within_limit(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"max_duration_sec": 10.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.duration_within_limit"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_resolution_meets_minimum_passes_when_at_or_above_floor(media, workspace):
    # clean.mp4 is 320x240.
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"min_width": 320, "min_height": 240}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.resolution_meets_minimum"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_resolution_meets_minimum_fails_when_below_floor(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"min_width": 1920, "min_height": 1080}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.resolution_meets_minimum"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "VIDEO_RESOLUTION_BELOW_MINIMUM" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_resolution_meets_minimum_is_independent_of_exact_equality_check(media, workspace):
    # A caller may ask for "at least 320x240" without pinning an exact size -
    # resolution_matches_expected must not be produced when only min_* is set.
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"min_width": 100, "min_height": 100}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert "video.resolution_matches_expected" not in checks
    assert checks["video.resolution_meets_minimum"]["status"] == "PASS"


def test_constant_frame_rate_fixture_is_not_flagged_as_vfr(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)["video.variable_frame_rate_suspected"]
    assert m["value"] is False


def test_disallow_vfr_passes_for_a_constant_frame_rate_fixture(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"disallow_vfr": True}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.frame_rate_is_constant"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_color_transfer_check_is_unknown_when_the_container_carries_no_tag(media, workspace):
    # clean.mp4's raw testsrc2 source carries no color_transfer tag at all
    # (ffprobe reports null) - a real gap real media can hit, exercised
    # end-to-end here; the actual mismatch/match paths (real values on both
    # sides) are exercised directly against evaluate_video in
    # test_rules_unknown_guards.py, since a synthetic tagged fixture isn't
    # needed to prove that logic.
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"expected_color_transfer": "bt709"}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.color_transfer_matches_expected"]["status"] == "UNKNOWN"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "VIDEO_COLOR_TRANSFER_MISMATCH" not in codes


def test_container_size_bytes_matches_the_actual_file_size(media, workspace):
    # container.size_bytes must come from the resolved file's own stat(),
    # not solely trust ffprobe's self-reported (and sometimes absent or
    # approximate) format.size field.
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)["container.size_bytes"]
    assert m["value"] == media["clean"].stat().st_size
    assert m["source"] == "OBSERVED"
