from tests.helpers import checks_by_id, measurements_by_id, run


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


def test_container_size_bytes_matches_the_actual_file_size(media, workspace):
    # container.size_bytes must come from the resolved file's own stat(),
    # not solely trust ffprobe's self-reported (and sometimes absent or
    # approximate) format.size field.
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)["container.size_bytes"]
    assert m["value"] == media["clean"].stat().st_size
    assert m["source"] == "OBSERVED"
