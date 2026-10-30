"""Planned-cut accuracy (ADR-017): detected cut times (ffmpeg scdet)
compared against a caller's cut list, e.g. video-production-agent's IR.
The fixture has cuts at frames 25, 60, 90 of a 25 fps clip (1.0, 2.4,
3.6 s)."""

import pytest

from qc_skill.errors import QCError
from qc_skill.rules import match_cuts
from qc_skill.schemas import parse_request
from tests.helpers import checks_by_id, measurements_by_id, run


def _check(media, workspace, **rule):
    doc = {"operation": "check", "kind": "video", "input": str(media["cuts"]), "rules": {"video": rule}}
    resp = run(doc, workspace)
    return resp, checks_by_id(resp)["video.cuts_match_expected"]


def test_detected_cut_times_are_exact(media, workspace):
    resp, _ = _check(media, workspace, expected_cuts=[1.0, 2.4, 3.6])
    cuts = measurements_by_id(resp)["video.scene_cuts"]["value"]
    assert [c["frame"] for c in cuts] == [25, 60, 90]
    assert [c["time"] for c in cuts] == pytest.approx([1.0, 2.4, 3.6])


def test_exact_cut_list_passes(media, workspace):
    _, check = _check(media, workspace, expected_cuts=[1.0, 2.4, 3.6])
    assert check["status"] == "PASS"
    assert check["evidence"]["max_error_frames"] == 0.0
    assert len(check["evidence"]["matched"]) == 3
    assert check["evidence"]["missing"] == [] and check["evidence"]["extra"] == []


def test_one_frame_error_is_within_default_tolerance(media, workspace):
    _, check = _check(media, workspace, expected_cuts=[1.04, 2.4, 3.6])
    assert check["status"] == "PASS"
    assert check["evidence"]["max_error_frames"] == pytest.approx(1.0)


def test_two_frame_error_fails(media, workspace):
    resp, check = _check(media, workspace, expected_cuts=[1.08, 2.4, 3.6])
    assert check["status"] == "FAIL"
    assert check["evidence"]["max_error_frames"] == pytest.approx(2.0)
    assert any(f["code"] == "VIDEO_CUT_MISMATCH" for f in resp["report"]["findings"])


def test_missing_planned_cut_fails(media, workspace):
    _, check = _check(media, workspace, expected_cuts=[1.0, 2.4, 3.0, 3.6])
    assert check["status"] == "FAIL"
    assert check["evidence"]["missing"] == [3.0]


def test_extra_cut_allowed_by_default(media, workspace):
    _, check = _check(media, workspace, expected_cuts=[1.0, 2.4])
    assert check["status"] == "PASS"
    assert check["evidence"]["extra"] == pytest.approx([3.6])


def test_extra_cut_fails_when_disallowed(media, workspace):
    _, check = _check(media, workspace, expected_cuts=[1.0, 2.4], allow_extra_cuts=False)
    assert check["status"] == "FAIL"


def test_single_shot_with_empty_cut_list_passes(media, workspace):
    # clean.mp4 is one continuous testsrc2 shot: no cut detected at all.
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"expected_cuts": [], "allow_extra_cuts": False}},
    }
    resp = run(doc, workspace)
    assert measurements_by_id(resp)["video.scene_cuts"]["value"] == []
    assert checks_by_id(resp)["video.cuts_match_expected"]["status"] == "PASS"


def test_cuts_not_measured_unless_requested(media, workspace):
    resp = run({"operation": "check", "kind": "video", "input": str(media["cuts"])}, workspace)
    assert "video.scene_cuts" not in measurements_by_id(resp)
    assert "video.cuts_match_expected" not in checks_by_id(resp)


def test_match_cuts_is_deterministic_and_nearest_first():
    out = match_cuts([1.0, 1.1], [1.04, 1.12], 25.0, 0.5)
    assert [m["detected"] for m in out["matched"]] == [1.04, 1.12]
    assert out["missing"] == [] and out["extra"] == []


@pytest.mark.parametrize(
    "rule",
    [
        {"expected_cuts": "1.0"},
        {"expected_cuts": [1.0, "2"]},
        {"expected_cuts": [-1.0]},
        {"expected_cuts": [True]},
        {"allow_extra_cuts": "yes"},
        {"max_cut_error_frames": -1},
        {"cut_match_window_sec": 0},
    ],
)
def test_invalid_cut_rules_are_rejected(rule):
    with pytest.raises(QCError):
        parse_request({"operation": "check", "kind": "video", "input": "x.mp4", "rules": {"video": rule}})


def test_expected_cuts_are_sorted_on_parse():
    req = parse_request({"operation": "check", "kind": "video", "input": "x.mp4", "rules": {"video": {"expected_cuts": [3, 1.5]}}})
    assert req.video_rule.expected_cuts == [1.5, 3.0]
