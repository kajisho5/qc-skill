from tests.helpers import checks_by_id, measurements_by_id, run


def test_inspect_valid_subtitle(media, workspace):
    doc = {"operation": "inspect", "kind": "subtitle", "input": str(media["subtitle_valid"]), "reference_video": str(media["clean"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)
    assert m["subtitle.exists"]["value"] is True
    assert m["subtitle.format"]["value"] == "srt"
    assert m["subtitle.cue_count"]["value"] == 2
    assert m["subtitle.invalid_timestamps"]["value"] == []
    assert m["subtitle.overlapping_cues"]["value"] == []
    assert m["subtitle.empty_cues"]["value"] == []
    assert m["subtitle.duplicate_ids"]["value"] == []
    # subtitle ends at 3.8s, video is 4s -> delta of 0.2s.
    assert abs(m["subtitle.duration_delta_sec"]["value"] - 0.2) < 0.01


def test_valid_subtitle_baseline_checks_pass(media, workspace):
    doc = {"operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]), "reference_video": str(media["clean"])}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.timestamps_valid"]["status"] == "PASS"
    assert checks["subtitle.no_empty_cues"]["status"] == "PASS"
    assert checks["subtitle.no_duplicate_ids"]["status"] == "PASS"
    assert checks["subtitle.no_control_characters"]["status"] == "PASS"
    assert checks["subtitle.no_overlapping_cues"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_malformed_subtitle_fails_baseline_checks(media, workspace):
    doc = {"operation": "check", "kind": "subtitle", "input": str(media["subtitle_malformed"])}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.timestamps_valid"]["status"] == "FAIL"
    assert checks["subtitle.no_duplicate_ids"]["status"] == "FAIL"
    assert checks["subtitle.no_empty_cues"]["status"] == "WARN"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert {"SUBTITLE_INVALID_TIMESTAMP", "SUBTITLE_DUPLICATE_ID", "SUBTITLE_EMPTY_CUE"} <= codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_subtitle_duration_mismatch_detected(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_mismatch"]),
        "reference_video": str(media["clean"]),
        "rules": {"subtitle": {"max_duration_delta_sec": 0.5}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.duration_matches_video"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "SUBTITLE_DURATION_MISMATCH" in codes


def test_subtitle_duration_match_within_tolerance_passes(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "reference_video": str(media["clean"]),
        "rules": {"subtitle": {"max_duration_delta_sec": 1.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.duration_matches_video"]["status"] == "PASS"


def test_duration_check_is_unknown_without_reference_video(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"max_duration_delta_sec": 1.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.duration_matches_video"]["status"] == "UNKNOWN"
    assert checks["subtitle.duration_matches_video"]["reason"]


def test_require_subtitle_false_passes_when_input_would_be_absent(media, workspace, tmp_path):
    doc = {"operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]), "rules": {"subtitle": {"require_subtitle": True}}}
    resp = run(doc, workspace)
    assert checks_by_id(resp)["subtitle.presence_matches_expected"]["status"] == "PASS"


def test_line_length_and_gap_rules(media, workspace):
    long_line_srt = workspace / "long.srt"
    long_line_srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n"
        + ("x" * 80)
        + "\n\n2\n00:00:10,000 --> 00:00:11,000\nshort\n\n",
        encoding="utf-8",
    )
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(long_line_srt),
        "rules": {"subtitle": {"max_line_length": 42, "max_gap_sec": 2.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.line_length_within_limit"]["status"] == "WARN"
    assert checks["subtitle.gaps_within_limit"]["status"] == "WARN"
