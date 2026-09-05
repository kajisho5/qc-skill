"""Luminance-range excursions (Phase 4, ADR-013): a deterministic
per-frame pixel readout via ffmpeg's signalstats, not a probabilistic
classifier. Real-media E2E against a fixture with a known, exact
out-of-range Y value (250, verified directly against ffmpeg before this
fixture was written - see ADR-013).
"""

from tests.helpers import checks_by_id, measurements_by_id, run


def test_inspect_reports_only_incidental_excursions_for_clean_video(media, workspace):
    # testsrc2's own gradient pattern briefly dips a few Y values below 16
    # for a frame or two - a real, correctly-measured fact about this test
    # pattern (verified directly), not a defect in the measurement. This
    # asserts it stays small (a handful of brief segments), not that it is
    # exactly zero. It deliberately does not assert *how far* below 16 the
    # dip goes: that incidental value is a detail of testsrc2's own pixel
    # content as encoded by the local libx264 build, and was observed to
    # differ slightly between Linux and macOS CI runners (10 vs. lower) -
    # exactly the kind of platform-dependent encoder detail this test must
    # not depend on (see the loud_clipping.wav precedent in
    # tests/fixtures/generate.py for the same lesson learned earlier).
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    segments = measurements_by_id(resp)["video.luminance_excursions"]["value"]
    assert sum(s["duration"] for s in segments) < 1.0
    assert len(segments) < 10


def test_inspect_detects_the_illegal_segment(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["illegal_luminance"])}
    resp = run(doc, workspace)
    segments = measurements_by_id(resp)["video.luminance_excursions"]["value"]
    assert len(segments) == 1
    seg = segments[0]
    assert seg["max_y"] == 250
    # The illegal half starts at ~1.0s (25fps, 1s of legal content first).
    assert 0.9 < seg["start"] < 1.1
    assert 0.7 < seg["duration"] < 1.1


def test_check_fails_when_total_exceeds_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["illegal_luminance"]),
        "rules": {"video": {"max_total_luminance_excursion_sec": 0.1}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.luminance_within_legal_range"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "VIDEO_LUMINANCE_OUT_OF_RANGE" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_check_passes_when_within_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["illegal_luminance"]),
        "rules": {"video": {"max_total_luminance_excursion_sec": 5.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.luminance_within_legal_range"]["status"] == "PASS"


def test_clean_video_passes_with_a_reasonable_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"max_single_luminance_excursion_sec": 0.5, "max_total_luminance_excursion_sec": 1.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.luminance_within_legal_range"]["status"] == "PASS"


def test_clean_video_fails_with_a_zero_tolerance(media, workspace):
    # The inverse of the above: an unrealistically strict 0.0 tolerance
    # correctly FAILs on the same incidental excursions - honest behavior,
    # not a bug, given the caller explicitly asked for zero tolerance.
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"max_total_luminance_excursion_sec": 0.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["video.luminance_within_legal_range"]["status"] == "FAIL"


def test_no_check_produced_without_a_rule(media, workspace):
    doc = {"operation": "check", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    check_ids = {c["check_id"] for c in resp["report"]["checks"]}
    assert "video.luminance_within_legal_range" not in check_ids


def test_custom_legal_range_parameters_change_detection(media, workspace):
    # Widen the legal range so far that even the illegal_luminance fixture's
    # forced Y=250 no longer counts as an excursion.
    doc = {
        "operation": "inspect", "kind": "video", "input": str(media["illegal_luminance"]),
        "parameters": {"luminance_legal_max": 255},
    }
    resp = run(doc, workspace)
    segments = measurements_by_id(resp)["video.luminance_excursions"]["value"]
    assert segments == []
