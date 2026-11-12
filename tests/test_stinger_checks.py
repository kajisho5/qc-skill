"""Stinger cut point (ADR-024): the stinger's alpha at its stated cut
point, the stated cover window, and a delivered video's frames at its cut
times compared with the stinger's covered frame.

Fixtures (tests/fixtures/generate.py): ``stinger_wipe.mov`` is 64x36 at
30 fps, its graphic covers the left half for frames 0-12, three quarters
for 13-14, the whole picture for 15-17 (cut point 0.5 s, cover
[0.5, 0.6)) and the right half after. ``stinger_edit.mp4`` joins red and
blue at frame 60 (2.0 s) with the stinger's frame 15 on frame 60;
``stinger_edit_late.mp4`` places it one frame later."""

from __future__ import annotations

import dataclasses
import json
import shutil
from fractions import Fraction

import pytest

from qc_skill.engine import run_report
from qc_skill.errors import QCError
from qc_skill.measurements.stinger import _gray_depth, alpha_coverage, frame_at, opaque_threshold
from qc_skill.models import QCMeasurement
from qc_skill.rules import StingerCover, StingerRule, VideoRule, evaluate_video
from qc_skill.schemas import parse_request
from tests.helpers import CAPS, checks_by_id, make_ctx, measurements_by_id, run


def _stinger_doc(path, **stinger):
    return {"operation": "check", "kind": "video", "input": str(path), "rules": {"video": {"stinger": stinger}}}


def _alpha(resp):
    return measurements_by_id(resp)["video.stinger_cut_point_alpha"]["value"]


# --- pure parts ---------------------------------------------------------------


def test_opaque_threshold_is_253_of_255_at_every_bit_depth():
    assert opaque_threshold(8) == 253
    assert opaque_threshold(10) == 1015
    assert opaque_threshold(12) == 4063
    assert opaque_threshold(16) == 65021  # 253 * 257, the 16-bit scale video-editing-skill compares on
    for bits in (8, 10, 12, 16):
        top = (1 << bits) - 1
        t = opaque_threshold(bits)
        assert t * 255 >= 253 * top > (t - 1) * 255


def test_alpha_coverage_counts_pixels_below_the_threshold():
    assert alpha_coverage(bytes([255, 253, 253]), 8) == (0, 253, 253)
    assert alpha_coverage(bytes([255, 252, 0]), 8) == (2, 0, 253)
    assert alpha_coverage([65535, 65021, 65020], 16) == (1, 65020, 65021)
    assert alpha_coverage([4095, 4063, 4062], 12) == (1, 4062, 4063)


def test_frame_at_names_the_frame_and_its_distance():
    assert frame_at(0.5, Fraction(30)) == (15, 0.0)
    n, off = frame_at(0.51, Fraction(30))
    assert n == 15 and off == pytest.approx(0.3)
    n, off = frame_at(0.5005, Fraction(30000, 1001))  # frame 15 at 29.97 fps, stated to the millisecond
    assert n == 15 and off < 0.001


def test_alpha_plane_formats_read():
    assert _gray_depth("gray") == (8, "little")
    assert _gray_depth("gray10le") == (10, "little")
    assert _gray_depth("gray12le") == (12, "little")
    assert _gray_depth("gray16be") == (16, "big")
    assert _gray_depth("grayf32le") is None
    assert _gray_depth("gray10") is None


def _evaluate(rule, alpha=None, frames=None):
    measurements = {
        "video.stream_present": QCMeasurement("video.stream_present", "video", "stream_present", True),
        "video.decode_error_count": QCMeasurement("video.decode_error_count", "video", "decode_error_count", 0),
    }
    if alpha is not None:
        measurements["video.stinger_cut_point_alpha"] = QCMeasurement("video.stinger_cut_point_alpha", "video", "a", alpha)
    if frames is not None:
        measurements["video.stinger_cut_frames"] = QCMeasurement("video.stinger_cut_frames", "video", "f", frames)
    checks, findings = evaluate_video(measurements, VideoRule(stinger=rule), None)
    return {c.check_id: c for c in checks}, findings


@pytest.mark.parametrize("cut_point, status", [(0.5, "PASS"), (0.5999, "PASS"), (0.6, "FAIL"), (0.4999, "FAIL")])
def test_cover_window_is_start_inclusive_end_exclusive(cut_point, status):
    checks, findings = _evaluate(StingerRule(cut_point=cut_point, cover=StingerCover(0.5, 0.6)))
    assert checks["video.stinger_cut_point_in_cover"].status.value == status
    if status == "FAIL":
        assert findings[0].code == "VIDEO_STINGER_CUT_POINT_OUTSIDE_COVER"
        assert findings[0].timestamp == cut_point


def test_unmeasured_alpha_is_unknown_never_opaque():
    checks, _ = _evaluate(StingerRule(cut_point=0.5))
    assert checks["video.stinger_opaque_at_cut_point"].status.value == "UNKNOWN"
    checks, _ = _evaluate(StingerRule(cut_point=0.5), alpha={"measured": False, "reason": "no alpha here"})
    assert checks["video.stinger_opaque_at_cut_point"].reason == "no alpha here"


def test_cut_frames_failure_outranks_an_unmeasured_cut():
    rule = StingerRule(cut_point=0.5, file="s.mov", cut_at=[1.0, 2.0], max_cut_frame_difference=2.0)
    frames = {"stinger_frame": {"measured": True}, "cuts": [
        {"cut_at": 1.0, "frame": 30, "measured": False, "reason": "no decodable frame"},
        {"cut_at": 2.0, "frame": 60, "measured": True, "difference": 2.5, "difference_before": 9.0, "difference_after": 1.0},
    ]}
    checks, findings = _evaluate(rule, frames=frames)
    assert checks["video.stinger_cut_frame_matches"].status.value == "FAIL"
    assert findings[-1].code == "VIDEO_STINGER_CUT_FRAME_MISMATCH" and findings[-1].timestamp == 2.0
    frames["cuts"][1]["difference"] = 2.0  # exactly the limit passes
    checks, _ = _evaluate(rule, frames=frames)
    assert checks["video.stinger_cut_frame_matches"].status.value == "UNKNOWN"
    assert "1.0s: no decodable frame" in checks["video.stinger_cut_frame_matches"].reason


# --- request schema ---------------------------------------------------------


@pytest.mark.parametrize("stinger", [
    {},
    {"cover": {"start": 0.5, "end": 0.6}},
    {"cut_point": -0.1},
    {"cut_point": "0.5"},
    {"cut_point": 0.5, "cover": {"start": 0.6, "end": 0.5}},
    {"cut_point": 0.5, "cover": {"start": 0.5}},
    {"cut_point": 0.5, "cover": [0.5, 0.6]},
    {"cut_point": 0.5, "cut_at": [2.0], "max_cut_frame_difference": 2},
    {"cut_point": 0.5, "file": "s.mov", "cut_at": [2.0]},
    {"cut_point": 0.5, "file": "s.mov", "cut_at": [], "max_cut_frame_difference": 2},
    {"cut_point": 0.5, "file": "s.mov", "max_cut_frame_difference": 2},
    {"cut_point": 0.5, "file": "s.mov", "cut_at": [2.0], "max_cut_frame_difference": 256},
    {"cut_point": 0.5, "file": ""},
    {"cut_point": 0.5, "frame": 15},
])
def test_invalid_stinger_rules_are_refused(stinger):
    with pytest.raises(QCError) as exc:
        parse_request(_stinger_doc("x.mov", **stinger))
    assert exc.value.code == "INVALID_REQUEST"


def test_stinger_rule_parses_and_sorts_cut_times():
    req = parse_request(_stinger_doc(
        "x.mp4", cut_point=0.5, cover={"start": 0.5, "end": 0.6}, file="s.mov", cut_at=[4, 2.0], max_cut_frame_difference=3,
    ))
    rule = req.video_rule.stinger
    assert rule.cut_at == [2.0, 4.0] and rule.cover == StingerCover(0.5, 0.6) and rule.max_cut_frame_difference == 3.0


# --- the stinger file itself ------------------------------------------------


def test_stinger_opaque_at_its_cut_point_passes(media, workspace):
    resp = run(_stinger_doc(media["stinger_wipe"], cut_point=0.5, cover={"start": 0.5, "end": 0.6}), workspace)
    checks = checks_by_id(resp)
    assert checks["video.stinger_cut_point_in_cover"]["status"] == "PASS"
    assert checks["video.stinger_opaque_at_cut_point"]["status"] == "PASS"
    alpha = _alpha(resp)
    assert alpha["measured"] is True and alpha["source"] == "input"
    assert (alpha["frame"], alpha["frame_time"], alpha["frame_rate"]) == (15, 0.5, "30/1")
    assert (alpha["pixels"], alpha["pixels_below"], alpha["min_alpha"]) == (64 * 36, 0, 255)
    assert alpha["threshold"] == {"min_alpha_255": 253, "scale": 255, "native": 253}
    json.dumps(resp)


def test_partly_transparent_cut_frame_fails_with_seconds_and_counts(media, workspace):
    resp = run(_stinger_doc(media["stinger_wipe"], cut_point=14 / 30), workspace)
    check = checks_by_id(resp)["video.stinger_opaque_at_cut_point"]
    assert check["status"] == "FAIL"
    alpha = check["evidence"]["alpha"]
    assert (alpha["frame"], alpha["pixels_below"], alpha["min_alpha"]) == (14, 16 * 36, 0)
    finding = next(f for f in resp["report"]["findings"] if f["code"] == "VIDEO_STINGER_NOT_OPAQUE")
    assert finding["timestamp"] == pytest.approx(14 / 30)
    assert finding["message"].startswith("0.467s frame 14 of the stinger: 576 of 2304 pixels")


def test_cut_point_outside_the_stated_cover_fails(media, workspace):
    resp = run(_stinger_doc(media["stinger_wipe"], cut_point=0.5, cover={"start": 0.4, "end": 0.5}), workspace)
    checks = checks_by_id(resp)
    assert checks["video.stinger_cut_point_in_cover"]["status"] == "FAIL"
    assert checks["video.stinger_opaque_at_cut_point"]["status"] == "PASS"  # declared and measured are told apart
    assert any(f["code"] == "VIDEO_STINGER_CUT_POINT_OUTSIDE_COVER" for f in resp["report"]["findings"])


def test_alpha_exactly_253_passes_and_252_fails(media, workspace):
    resp = run(_stinger_doc(media["stinger_edge"], cut_point=10 / 30), workspace)
    assert checks_by_id(resp)["video.stinger_opaque_at_cut_point"]["status"] == "PASS"
    assert _alpha(resp)["min_alpha"] == 253
    resp = run(_stinger_doc(media["stinger_edge"], cut_point=20 / 30), workspace)
    assert checks_by_id(resp)["video.stinger_opaque_at_cut_point"]["status"] == "FAIL"
    assert (_alpha(resp)["pixels_below"], _alpha(resp)["min_alpha"]) == (1, 252)


def test_prores_4444_alpha_is_read_at_its_own_bit_depth(media, workspace):
    resp = run(_stinger_doc(media["stinger_prores"], cut_point=0.5), workspace)
    assert checks_by_id(resp)["video.stinger_opaque_at_cut_point"]["status"] == "PASS"
    alpha = _alpha(resp)
    assert alpha["alpha_bit_depth"] > 8 and alpha["min_alpha_255"] == 255.0
    assert alpha["threshold"]["native"] == opaque_threshold(alpha["alpha_bit_depth"])
    resp = run(_stinger_doc(media["stinger_prores"], cut_point=14 / 30), workspace)
    assert checks_by_id(resp)["video.stinger_opaque_at_cut_point"]["status"] == "FAIL"
    assert _alpha(resp)["pixels_below"] == 16 * 36


def test_video_without_alpha_is_unknown_not_opaque(media, workspace):
    resp = run(_stinger_doc(media["clean"], cut_point=1.0), workspace)
    check = checks_by_id(resp)["video.stinger_opaque_at_cut_point"]
    assert check["status"] == "UNKNOWN"
    assert check["reason"] == "the stream's pixel format 'yuv420p' has no alpha channel"


def test_cut_point_between_frames_is_unknown(media, workspace):
    resp = run(_stinger_doc(media["stinger_wipe"], cut_point=0.51), workspace)
    check = checks_by_id(resp)["video.stinger_opaque_at_cut_point"]
    assert check["status"] == "UNKNOWN"
    assert "is not on a frame" in check["reason"] and "frame 15" in check["reason"]


def test_cut_point_past_the_end_is_unknown(media, workspace):
    resp = run(_stinger_doc(media["stinger_wipe"], cut_point=5.0), workspace)
    check = checks_by_id(resp)["video.stinger_opaque_at_cut_point"]
    assert check["status"] == "UNKNOWN"
    assert check["reason"].startswith("no decodable frame at frame 150")


def test_no_stinger_rule_measures_nothing(media, workspace):
    resp = run({"operation": "check", "kind": "video", "input": str(media["stinger_wipe"])}, workspace)
    assert not any(k.startswith("video.stinger_") for k in measurements_by_id(resp))
    assert not any(c["check_id"].startswith("video.stinger_") for c in resp["report"]["checks"])


def test_without_ffmpeg_the_stinger_checks_are_unknown(media, workspace):
    ctx = make_ctx(workspace)
    ctx.capabilities = dataclasses.replace(CAPS, ffmpeg_path="")
    resp = run_report(parse_request(_stinger_doc(media["stinger_wipe"], cut_point=0.5)), ctx)
    check = checks_by_id(resp)["video.stinger_opaque_at_cut_point"]
    assert check["status"] == "UNKNOWN" and check["reason"] == "ffmpeg is not available"


# --- a delivered video and the stinger file ---------------------------------


def _edit_doc(media, name, *, stinger="stinger_wipe", cut_at=(2.0,), limit=4.0, **extra):
    return _stinger_doc(
        media[name], cut_point=0.5, file=str(media[stinger]), cut_at=list(cut_at), max_cut_frame_difference=limit, **extra,
    )


def test_delivered_cut_frame_shows_the_stinger_cover(media, workspace):
    resp = run(_edit_doc(media, "stinger_edit"), workspace)
    checks = checks_by_id(resp)
    assert checks["video.stinger_cut_frame_matches"]["status"] == "PASS"
    assert checks["video.stinger_opaque_at_cut_point"]["status"] == "PASS"
    assert _alpha(resp)["source"] == "stinger_file"
    frames = measurements_by_id(resp)["video.stinger_cut_frames"]["value"]
    assert frames["stinger_frame"]["frame"] == 15
    cut = frames["cuts"][0]
    assert (cut["frame"], cut["frame_time"]) == (60, 2.0)
    assert cut["difference"] <= 4.0 < cut["difference_before"]  # frame 59 still shows a quarter of the red clip
    assert resp["provenance"]["stinger_input"]["fingerprint"]


def test_stinger_placed_one_frame_late_fails(media, workspace):
    resp = run(_edit_doc(media, "stinger_edit_late"), workspace)
    check = checks_by_id(resp)["video.stinger_cut_frame_matches"]
    assert check["status"] == "FAIL"
    finding = next(f for f in resp["report"]["findings"] if f["code"] == "VIDEO_STINGER_CUT_FRAME_MISMATCH")
    assert finding["timestamp"] == 2.0
    violation = finding["evidence"]["violations"][0]
    assert violation["frame"] == 60 and violation["difference"] > 4.0
    assert violation["description"].startswith("2.000s frame 60 differs from the stinger's frame at its cut point by")


def test_cut_frame_difference_exactly_at_the_limit_passes(media, workspace):
    resp = run(_edit_doc(media, "stinger_edit_late", limit=255), workspace)
    measured = measurements_by_id(resp)["video.stinger_cut_frames"]["value"]["cuts"][0]["difference"]
    resp = run(_edit_doc(media, "stinger_edit_late", limit=measured), workspace)
    assert checks_by_id(resp)["video.stinger_cut_frame_matches"]["status"] == "PASS"
    resp = run(_edit_doc(media, "stinger_edit_late", limit=round(measured - 0.001, 3)), workspace)
    assert checks_by_id(resp)["video.stinger_cut_frame_matches"]["status"] == "FAIL"


def test_high_bit_depth_stinger_compares_the_same(media, workspace):
    resp = run(_edit_doc(media, "stinger_edit", stinger="stinger_prores"), workspace)
    assert checks_by_id(resp)["video.stinger_cut_frame_matches"]["status"] == "PASS"


@pytest.mark.parametrize("cut_at, reason", [
    (2.01, "the cut time 2.01s is not on a frame of the delivered video"),
    (10.0, "no decodable frame at frame 300"),
])
def test_unmeasurable_cut_time_is_unknown(media, workspace, cut_at, reason):
    resp = run(_edit_doc(media, "stinger_edit", cut_at=(cut_at,)), workspace)
    check = checks_by_id(resp)["video.stinger_cut_frame_matches"]
    assert check["status"] == "UNKNOWN"
    assert reason in check["reason"]


def test_one_good_and_one_unmeasurable_cut_is_unknown(media, workspace):
    resp = run(_edit_doc(media, "stinger_edit", cut_at=(2.0, 10.0)), workspace)
    check = checks_by_id(resp)["video.stinger_cut_frame_matches"]
    assert check["status"] == "UNKNOWN"
    assert [c["measured"] for c in check["evidence"]["cuts"]] == [True, False]


def test_unreadable_stinger_file_is_unknown(media, workspace):
    bogus = workspace / "not_a_stinger.mov"
    bogus.write_text("not media", encoding="utf-8")
    resp = run(_stinger_doc(media["stinger_edit"], cut_point=0.5, file=str(bogus), cut_at=[2.0], max_cut_frame_difference=4), workspace)
    checks = checks_by_id(resp)
    for check_id in ("video.stinger_opaque_at_cut_point", "video.stinger_cut_frame_matches"):
        assert checks[check_id]["status"] == "UNKNOWN"
    assert checks["video.stinger_opaque_at_cut_point"]["reason"].startswith("the stinger file could not be read")


def test_stinger_file_without_video_is_unknown(media, workspace):
    resp = run(_stinger_doc(media["stinger_edit"], cut_point=0.5, file=str(media["silence_gap"])), workspace)
    check = checks_by_id(resp)["video.stinger_opaque_at_cut_point"]
    assert check["status"] == "UNKNOWN" and check["reason"] == "the stinger file has no video stream"


def test_missing_stinger_file_is_a_request_error(media, workspace):
    with pytest.raises(QCError) as exc:
        run(_stinger_doc(media["stinger_edit"], cut_point=0.5, file=str(workspace / "nope.mov")), workspace)
    assert exc.value.code == "MISSING_INPUT"


def test_stinger_file_outside_the_allowed_roots_is_refused(media, workspace):
    with pytest.raises(QCError) as exc:
        run(_edit_doc(media, "stinger_edit"), workspace, allowed_roots=[str(workspace)])
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_identity_follows_the_stinger_content_not_its_path(media, workspace):
    moved = workspace / "elsewhere.mov"
    shutil.copy(media["stinger_wipe"], moved)

    def key(path):
        doc = _stinger_doc(media["stinger_edit"], cut_point=0.5, file=str(path), cut_at=[2.0], max_cut_frame_difference=4)
        doc["cache_policy"] = "bypass"
        return run(doc, workspace)["cache"]["key"]

    assert key(media["stinger_wipe"]) == key(moved)
    shutil.copy(media["stinger_edge"], moved)
    assert key(media["stinger_wipe"]) != key(moved)


def test_delivery_package_checks_the_stinger_and_the_edit(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "stinger", "artifact_type": "video", "path": str(media["stinger_wipe"])},
            {"artifact_id": "edit", "artifact_type": "video", "path": str(media["stinger_edit_late"])},
        ],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "stinger", "video": {"stinger": {"cut_point": 0.5, "cover": {"start": 0.5, "end": 0.6}}}},
            {"artifact_id": "edit", "video": {"stinger": {
                "cut_point": 0.5, "file": str(media["stinger_wipe"]), "cut_at": [2.0], "max_cut_frame_difference": 4,
            }}},
        ]}},
    }
    resp = run(doc, workspace)
    statuses = {(c["artifact_id"], c["check_id"]): c["status"] for c in resp["report"]["checks"] if "stinger" in c["check_id"]}
    assert statuses[("stinger", "video.stinger_opaque_at_cut_point")] == "PASS"
    assert statuses[("stinger", "video.stinger_cut_point_in_cover")] == "PASS"
    assert statuses[("edit", "video.stinger_cut_frame_matches")] == "FAIL"
    by_id = {a["artifact_id"]: a for a in resp["provenance"]["artifacts"]}
    assert "stinger_fingerprint" in by_id["edit"] and "stinger_fingerprint" not in by_id["stinger"]


def test_delivery_video_rule_runs_the_stinger_checks(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery", "input": str(media["stinger_edit"]),
        "rules": {"delivery": {"video": {"stinger": {
            "cut_point": 0.5, "file": str(media["stinger_wipe"]), "cut_at": [2.0], "max_cut_frame_difference": 4,
        }}}},
    }
    resp = run(doc, workspace)
    assert checks_by_id(resp)["video.stinger_cut_frame_matches"]["status"] == "PASS"
