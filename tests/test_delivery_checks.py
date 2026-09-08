from qc_skill.errors import QCError
from tests.helpers import checks_by_id, run

import pytest


def test_delivery_full_pass(media, workspace):
    doc = {
        "operation": "validate", "kind": "delivery", "input": str(media["clean"]), "subtitle": str(media["subtitle_valid"]),
        "rules": {
            "delivery": {
                "expected_extension": "mp4", "min_size_bytes": 10,
                "video": {"expected_width": 320, "expected_height": 240},
                "audio": {"require_audio_stream": True},
                "subtitle": {"max_duration_delta_sec": 1.0},
            }
        },
    }
    resp = run(doc, workspace)
    assert resp["report"]["overall_status"] == "PASS"
    checks = checks_by_id(resp)
    assert checks["delivery.extension_matches_expected"]["status"] == "PASS"
    assert checks["video.resolution_matches_expected"]["status"] == "PASS"
    assert checks["audio.stream_present_matches_expected"]["status"] == "PASS"
    assert checks["subtitle.duration_matches_video"]["status"] == "PASS"


def test_delivery_extension_mismatch_fails(media, workspace):
    doc = {"operation": "validate", "kind": "delivery", "input": str(media["clean"]), "rules": {"delivery": {"expected_extension": "mov"}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["delivery.extension_matches_expected"]["status"] == "FAIL"


def test_delivery_min_size_fails_for_tiny_requirement_violation(media, workspace):
    size = media["clean"].stat().st_size
    doc = {"operation": "validate", "kind": "delivery", "input": str(media["clean"]), "rules": {"delivery": {"min_size_bytes": size + 1000}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["delivery.file_size_within_limit"]["status"] == "FAIL"


def test_delivery_max_size_fails_when_over_the_ceiling(media, workspace):
    size = media["clean"].stat().st_size
    doc = {"operation": "validate", "kind": "delivery", "input": str(media["clean"]), "rules": {"delivery": {"max_size_bytes": size - 1}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["delivery.file_size_within_limit"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_FILE_TOO_LARGE" in codes


def test_delivery_max_size_passes_when_within_the_ceiling(media, workspace):
    size = media["clean"].stat().st_size
    doc = {"operation": "validate", "kind": "delivery", "input": str(media["clean"]), "rules": {"delivery": {"max_size_bytes": size + 1000}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["delivery.file_size_within_limit"]["status"] == "PASS"


def test_delivery_min_and_max_size_together_report_both_violations_if_impossible(media, workspace):
    # A deliberately-contradictory rule (max below min) must surface both
    # findings on the one check, not silently pick one.
    doc = {
        "operation": "validate", "kind": "delivery", "input": str(media["clean"]),
        "rules": {"delivery": {"min_size_bytes": 10**9, "max_size_bytes": 1}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["delivery.file_size_within_limit"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_FILE_TOO_SMALL" in codes
    assert "DELIVERY_FILE_TOO_LARGE" in codes


def test_delivery_missing_subtitle_when_required(media, workspace):
    doc = {"operation": "validate", "kind": "delivery", "input": str(media["clean"]), "rules": {"delivery": {"require_subtitle": True}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.presence_matches_expected"]["status"] == "FAIL"
    assert resp["report"]["overall_status"] == "FAIL"


def test_delivery_missing_input_file_raises_missing_input(workspace):
    doc = {"operation": "inspect", "kind": "delivery", "input": str(workspace / "does_not_exist.mp4")}
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "MISSING_INPUT"


def test_delivery_container_matches_expected(media, workspace):
    doc = {"operation": "check", "kind": "delivery", "input": str(media["clean"]), "rules": {"delivery": {"expected_container": "mp4"}}}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["delivery.container_matches_expected"]["status"] == "PASS"
