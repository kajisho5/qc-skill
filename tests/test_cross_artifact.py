"""Cross-artifact validation (Phase 2, ADR-011): typed relationship rules
comparing different artifacts in a delivery_package against each other.
Real-media E2E, not synthetic JSON only.
"""

import pytest

from qc_skill.errors import QCError

from tests.helpers import run


def _checks(resp):
    return [c for c in resp["report"]["checks"] if c["check_id"] in (
        "delivery_package.duration_consistent", "delivery_package.dependency_satisfied",
    )]


def _check_by_id(resp, check_id):
    matches = [c for c in resp["report"]["checks"] if c["check_id"] == check_id]
    assert len(matches) == 1, f"expected exactly one {check_id!r}, got {len(matches)}"
    return matches[0]


def test_duration_consistency_passes_within_tolerance(media, workspace):
    # clean.mp4 is 4s; subtitle_valid.srt's last cue ends at 3.8s -> delta 0.2s.
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(media["subtitle_valid"])},
        ],
        "rules": {"delivery_package": {"cross_artifact": {"duration_consistency": [
            {"artifact_ids": ["main_video", "captions"], "max_delta_sec": 1.0},
        ]}}},
    }
    resp = run(doc, workspace)
    check = _check_by_id(resp, "delivery_package.duration_consistent")
    assert check["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_duration_consistency_fails_outside_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(media["subtitle_mismatch"])},
        ],
        "rules": {"delivery_package": {"cross_artifact": {"duration_consistency": [
            {"artifact_ids": ["main_video", "captions"], "max_delta_sec": 0.5},
        ]}}},
    }
    resp = run(doc, workspace)
    check = _check_by_id(resp, "delivery_package.duration_consistent")
    assert check["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_PACKAGE_DURATION_MISMATCH" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_duration_consistency_is_unknown_when_an_artifact_is_absent(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(workspace / "missing.srt")},
        ],
        "rules": {"delivery_package": {"cross_artifact": {"duration_consistency": [
            {"artifact_ids": ["main_video", "captions"], "max_delta_sec": 1.0},
        ]}}},
    }
    resp = run(doc, workspace)
    check = _check_by_id(resp, "delivery_package.duration_consistent")
    assert check["status"] == "UNKNOWN"
    assert check["reason"]
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_PACKAGE_DURATION_MISMATCH" not in codes


def test_duration_consistency_across_three_artifacts(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "audio_only", "artifact_type": "audio", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(media["subtitle_valid"])},
        ],
        "rules": {"delivery_package": {"cross_artifact": {"duration_consistency": [
            {"artifact_ids": ["main_video", "audio_only", "captions"], "max_delta_sec": 1.0},
        ]}}},
    }
    resp = run(doc, workspace)
    check = _check_by_id(resp, "delivery_package.duration_consistent")
    assert check["status"] == "PASS"
    assert set(check["evidence"]["durations"].keys()) == {"main_video", "audio_only", "captions"}


def test_dependency_satisfied_when_both_present(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(media["subtitle_valid"])},
        ],
        "rules": {"delivery_package": {"cross_artifact": {"dependencies": [
            {"artifact_id": "main_video", "requires_artifact_id": "captions"},
        ]}}},
    }
    resp = run(doc, workspace)
    check = _check_by_id(resp, "delivery_package.dependency_satisfied")
    assert check["status"] == "PASS"


def test_dependency_missing_fails(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(workspace / "missing.srt")},
        ],
        "rules": {"delivery_package": {"cross_artifact": {"dependencies": [
            {"artifact_id": "main_video", "requires_artifact_id": "captions"},
        ]}}},
    }
    resp = run(doc, workspace)
    check = _check_by_id(resp, "delivery_package.dependency_satisfied")
    assert check["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_PACKAGE_DEPENDENCY_MISSING" in codes


def test_dependency_not_enforced_when_dependent_itself_absent(media, workspace):
    # main_video is absent -> nothing to enforce about what it "requires".
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(workspace / "missing.mp4")},
        ],
        "rules": {"delivery_package": {"cross_artifact": {"dependencies": [
            {"artifact_id": "main_video", "requires_artifact_id": "captions"},
        ]}}},
    }
    resp = run(doc, workspace)
    assert len(_checks(resp)) == 0


def test_cross_artifact_and_per_artifact_checks_coexist(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(media["subtitle_valid"])},
        ],
        "rules": {"delivery_package": {
            "artifacts": [{"artifact_id": "main_video", "required": True}],
            "cross_artifact": {"duration_consistency": [
                {"artifact_ids": ["main_video", "captions"], "max_delta_sec": 1.0},
            ]},
        }},
    }
    resp = run(doc, workspace)
    check_ids = {c["check_id"] for c in resp["report"]["checks"]}
    assert "delivery_package.artifact_present" in check_ids
    assert "delivery_package.duration_consistent" in check_ids


def test_duration_consistency_requires_at_least_two_artifact_ids(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"cross_artifact": {"duration_consistency": [
            {"artifact_ids": ["main_video"], "max_delta_sec": 1.0},
        ]}}},
    }
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "INVALID_REQUEST"
