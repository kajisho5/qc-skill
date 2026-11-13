"""Title, stinger and loop material (ADR-026/027/028): a video loop's seam,
alpha edges at the declared mode, and a key / fill pair.

Fixtures (tests/fixtures/generate.py, ``_build_video_material_fixtures``),
all 64x36 at 30 fps:

* ``vloop_orbit`` - a soft dot once round an ellipse in 30 frames (a
  seamless loop); ``vloop_pan`` - a 32 px periodic texture panned 1 px a
  frame for 32 frames (a pan cut on a whole period); ``_short`` drops the
  last frame of each; ``vloop_title`` is IN 10 frames, the orbit as LOOP
  (30 frames), OUT 10 frames; ``vloop_undecodable`` probes but decodes
  nothing.
* ``alpha_straight`` - grey 230 with alpha ramping over x = 16..22,
  straight; ``alpha_premultiplied`` is the same premultiplied.
* ``kf_fill`` / ``kf_key`` - ffmpeg-skill's ``export --preset key-fill``
  chain on a moving box; ``kf_key_late`` is the key a frame behind;
  ``kf_lit_fill`` has one pixel lit under alpha 0 on frame 10.
"""

from __future__ import annotations

import dataclasses
import json
import shutil

import pytest

from qc_skill.engine import run_report
from qc_skill.errors import QCError
from qc_skill.measurements.alpha_edge import composite, fringe_facts, fringe_width, mode_facts
from qc_skill.models import QCMeasurement
from qc_skill.rules import AlphaEdgeRule, VideoLoopRule, VideoRule, evaluate_video
from qc_skill.schemas import parse_request
from tests.helpers import CAPS, checks_by_id, make_ctx, measurements_by_id, run

TITLE_SEGMENTS = {"in": 0.333333, "loop": 1.0, "out": 0.333333}


def _doc(path, **video):
    return {"operation": "check", "kind": "video", "input": str(path), "rules": {"video": video}}


def _value(resp, measurement_id):
    return measurements_by_id(resp)[measurement_id]["value"]


def _finding(resp, code):
    return next(f for f in resp["report"]["findings"] if f["code"] == code)


# --- pure parts ---------------------------------------------------------------


def test_composite_at_each_mode():
    assert composite(230, 255, 0, "straight") == 230
    assert composite(230, 0, 255, "straight") == 255
    assert composite(230, 51, 0, "straight") == pytest.approx(46)
    assert composite(46, 51, 0, "premultiplied") == 46
    assert composite(230, 51, 255, "premultiplied") == 255  # clipped


def test_mode_facts_count_colours_above_alpha_with_two_of_tolerance():
    raw = bytes([0, 0, 0, 0, 10, 10, 10, 8, 11, 0, 0, 8, 255, 255, 255, 255])
    facts = mode_facts(raw)
    assert facts["over_alpha_pixels"] == 1
    assert facts["worst"] == (3, 2, 8)


def test_fringe_width_is_the_shorter_run():
    width, height = 10, 10
    band = [y * width + x for y in range(height) for x in (3, 4, 5)]  # a vertical band 3 px wide
    assert fringe_width(band, width, height) == 3
    assert fringe_width([0], width, height) == 1
    assert fringe_width([], width, height) == 0


def test_a_hard_edge_has_nothing_to_judge():
    raw = bytes([230, 230, 230, 255] * 4 + [0, 0, 0, 0] * 4)
    facts = fringe_facts(raw, 4, 2, "straight")
    assert facts["partial_pixels"] == 0 and facts["width_px"] == 0


def _evaluate(rule, **values):
    measurements = {
        "video.stream_present": QCMeasurement("video.stream_present", "video", "stream_present", True),
        "video.decode_error_count": QCMeasurement("video.decode_error_count", "video", "decode_error_count", 0),
    }
    for mid, value in values.items():
        mid = "video." + mid
        measurements[mid] = QCMeasurement(mid, "video", mid, value)
    checks, findings = evaluate_video(measurements, VideoRule(**rule), None)
    return {c.check_id: c for c in checks}, findings


def _seam(ratio, still=False, difference=6.0):
    return {
        "measured": True, "ratio": ratio, "still": still, "reference": 3.0, "inside": [], "loop": {},
        "seam": {"from_frame": 29, "to_frame": 0, "from_time": 29 / 30, "to_time": 0.0, "difference": difference},
    }


@pytest.mark.parametrize("ratio, status", [(1.3, "PASS"), (1.301, "FAIL"), (0.0, "PASS")])
def test_seam_ratio_limit_is_inclusive(ratio, status):
    checks, _ = _evaluate({"loop_seam": VideoLoopRule(max_seam_ratio=1.3)}, loop_seam=_seam(ratio))
    assert checks["video.loop_seam_within_limit"].status.value == status


def test_a_jump_out_of_a_still_picture_fails():
    checks, findings = _evaluate({"loop_seam": VideoLoopRule(max_seam_ratio=100)}, loop_seam=_seam(None, still=True))
    assert checks["video.loop_seam_within_limit"].status.value == "FAIL"
    assert "out of a still picture" in findings[-1].message


def test_loop_seam_without_a_limit_is_measured_not_judged():
    checks, _ = _evaluate({"loop_seam": VideoLoopRule()}, loop_seam=_seam(5.0))
    assert "video.loop_seam_within_limit" not in checks


def test_alpha_edge_without_max_fringe_runs_only_the_mode_check():
    value = {"measured": True, "frames": []}
    checks, _ = _evaluate({"alpha_edge": AlphaEdgeRule(alpha_mode="straight")}, alpha_edges=value)
    assert "video.alpha_mode_consistent" in checks and "video.alpha_edge_clean" not in checks


# --- request schema ---------------------------------------------------------


@pytest.mark.parametrize("video", [
    {"loop_seam": []},
    {"loop_seam": {"window": 4}},
    {"loop_seam": {"window_frames": 0}},
    {"loop_seam": {"window_frames": 13}},
    {"loop_seam": {"window_frames": 2.5}},
    {"loop_seam": {"max_seam_ratio": -1}},
    {"loop_seam": {"segments": {"in": 0, "loop": 1}}},
    {"loop_seam": {"segments": {"in": 0, "loop": 0, "out": 0}}},
    {"loop_seam": {"segments": {"in": -1, "loop": 1, "out": 0}}},
    {"alpha_edge": {}},
    {"alpha_edge": {"alpha_mode": "linear"}},
    {"alpha_edge": {"alpha_mode": "straight", "times": []}},
    {"alpha_edge": {"alpha_mode": "straight", "max_fringe_px": 1.5}},
    {"alpha_edge": {"alpha_mode": "straight", "max_fringe_px": -1}},
    {"alpha_edge": {"alpha_mode": "straight", "sample": 3}},
    {"key_fill": {}},
    {"key_fill": {"key_file": ""}},
    {"key_fill": {"key_file": "k.mov", "tolerance": 255}},
    {"key_fill": {"key_file": "k.mov", "max_off_fraction": 1}},
    {"key_fill": {"key_file": "k.mov", "key_range": "tv"}},
    {"key_fill": {"key": "k.mov"}},
])
def test_invalid_material_rules_are_refused(video):
    with pytest.raises(QCError) as exc:
        parse_request(_doc("x.mov", **video))
    assert exc.value.code == "INVALID_REQUEST"


def test_material_rules_parse():
    req = parse_request(_doc(
        "x.mov",
        loop_seam={"window_frames": 6, "segments": {"in": 0.5, "loop": 2, "out": 0}, "max_seam_ratio": 1.3},
        alpha_edge={"alpha_mode": "premultiplied", "times": [1, 0.5, 1], "max_fringe_px": 0},
        key_fill={"key_file": "k.mov", "tolerance": 4, "max_off_fraction": 0.001, "key_range": "full"},
    ))
    rule = req.video_rule
    assert rule.loop_seam == VideoLoopRule(window_frames=6, segments={"in": 0.5, "loop": 2.0, "out": 0.0}, max_seam_ratio=1.3)
    assert rule.alpha_edge.times == [0.5, 1.0] and rule.alpha_edge.max_fringe_px == 0
    assert (rule.key_fill.tolerance, rule.key_fill.key_range) == (4, "full")


# --- step 11: video loop seam -----------------------------------------------


@pytest.mark.parametrize("name", ["vloop_orbit", "vloop_pan"])
def test_seamless_loop_and_whole_period_pan_pass(media, workspace, name):
    resp = run(_doc(media[name], loop_seam={"max_seam_ratio": 1.3}), workspace)
    assert checks_by_id(resp)["video.loop_seam_within_limit"]["status"] == "PASS"
    value = _value(resp, "video.loop_seam")
    assert value["source"] == "file" and value["seam"]["to_frame"] == 0
    assert value["ratio"] <= 1.1
    assert len(value["inside"]) == 8  # window_frames 4 on each side
    json.dumps(resp)


@pytest.mark.parametrize("name, last", [("vloop_orbit_short", 28), ("vloop_pan_short", 30)])
def test_loop_cut_one_frame_short_fails_with_the_seam_frames(media, workspace, name, last):
    resp = run(_doc(media[name], loop_seam={"max_seam_ratio": 1.3}), workspace)
    check = checks_by_id(resp)["video.loop_seam_within_limit"]
    assert check["status"] == "FAIL"
    seam = check["evidence"]["seam"]
    assert (seam["from_frame"], seam["to_frame"]) == (last, 0)
    assert check["evidence"]["ratio"] > 1.5
    finding = _finding(resp, "VIDEO_LOOP_SEAM_JUMP")
    assert finding["timestamp"] == pytest.approx(last / 30, abs=1e-6)
    assert f"the loop's last frame {last} -> its first frame 0 steps by" in finding["message"]


def test_seam_ratio_edge_is_exact(media, workspace):
    measured = _value(run(_doc(media["vloop_pan_short"], loop_seam={}), workspace), "video.loop_seam")["ratio"]
    resp = run(_doc(media["vloop_pan_short"], loop_seam={"max_seam_ratio": measured}), workspace)
    assert checks_by_id(resp)["video.loop_seam_within_limit"]["status"] == "PASS"
    resp = run(_doc(media["vloop_pan_short"], loop_seam={"max_seam_ratio": round(measured - 0.001, 3)}), workspace)
    assert checks_by_id(resp)["video.loop_seam_within_limit"]["status"] == "FAIL"


def test_title_loop_segment_from_title_json(media, workspace):
    resp = run(_doc(media["vloop_title"], loop_seam={"segments": TITLE_SEGMENTS, "max_seam_ratio": 1.3}), workspace)
    assert checks_by_id(resp)["video.loop_seam_within_limit"]["status"] == "PASS"
    value = _value(resp, "video.loop_seam")
    assert value["source"] == "segments"
    assert value["loop"] == {"first_frame": 10, "last_frame": 39, "frames": 30}
    assert (value["seam"]["from_time"], value["seam"]["to_time"]) == (1.3, 0.333333)


def test_segment_one_frame_short_fails(media, workspace):
    segments = {"in": 0.333333, "loop": 29 / 30, "out": 0.366667}
    resp = run(_doc(media["vloop_title"], loop_seam={"segments": segments, "max_seam_ratio": 1.3}), workspace)
    check = checks_by_id(resp)["video.loop_seam_within_limit"]
    assert check["status"] == "FAIL"
    assert (check["evidence"]["seam"]["from_frame"], check["evidence"]["seam"]["to_frame"]) == (38, 10)


@pytest.mark.parametrize("name, reason", [
    ("vloop_one_frame", "the loop has 1 frame(s)"),
    ("vloop_undecodable", "no decodable frame at frame 0"),
])
def test_one_frame_or_undecodable_loop_is_unknown(media, workspace, name, reason):
    resp = run(_doc(media[name], loop_seam={"max_seam_ratio": 1.3}), workspace)
    check = checks_by_id(resp)["video.loop_seam_within_limit"]
    assert check["status"] == "UNKNOWN"
    assert check["reason"].startswith(reason)


@pytest.mark.parametrize("segments, reason", [
    ({"in": 0.34, "loop": 1.0, "out": 0}, "segments in = 0.34s is not on a frame"),
    ({"in": 0.333333, "loop": 3.0, "out": 0}, "no decodable frame at frame"),
])
def test_segments_off_frame_or_past_the_end_are_unknown(media, workspace, segments, reason):
    resp = run(_doc(media["vloop_title"], loop_seam={"segments": segments, "max_seam_ratio": 1.3}), workspace)
    check = checks_by_id(resp)["video.loop_seam_within_limit"]
    assert check["status"] == "UNKNOWN" and reason in check["reason"]


# --- step 12: alpha edge ----------------------------------------------------


def _edge_doc(media, name, mode, **extra):
    return _doc(media[name], alpha_edge={"alpha_mode": mode, "max_fringe_px": 1, **extra})


@pytest.mark.parametrize("name", ["alpha_straight", "alpha_straight_prores"])
def test_straight_file_declared_straight_passes(media, workspace, name):
    resp = run(_edge_doc(media, name, "straight"), workspace)
    checks = checks_by_id(resp)
    assert checks["video.alpha_mode_consistent"]["status"] == "PASS"
    assert checks["video.alpha_edge_clean"]["status"] == "PASS"
    frames = _value(resp, "video.alpha_edges")["frames"]
    assert [f["frame"] for f in frames] == [0, 7, 14, 22, 29]  # 5 frames spread over 30
    assert all(f["fringe"]["judged_pixels"] == 5 * 36 and f["fringe"]["width_px"] == 0 for f in frames)


def test_straight_file_declared_premultiplied_fails_the_mode_check(media, workspace):
    resp = run(_edge_doc(media, "alpha_straight", "premultiplied"), workspace)
    check = checks_by_id(resp)["video.alpha_mode_consistent"]
    assert check["status"] == "FAIL"
    assert [v["frame"] for v in check["evidence"]["violations"]] == [0, 7, 14, 22, 29]
    finding = _finding(resp, "VIDEO_ALPHA_MODE_MISMATCH")
    assert finding["message"].startswith("0.000s frame 0: 792 pixels have a colour above their alpha")
    # and its edges come out light when composited that way
    assert checks_by_id(resp)["video.alpha_edge_clean"]["status"] == "FAIL"
    assert "light fringe 5 px wide" in _finding(resp, "VIDEO_ALPHA_FRINGE")["message"]


def test_premultiplied_file_composited_as_straight_fails_the_fringe_check_with_its_width(media, workspace):
    resp = run(_edge_doc(media, "alpha_premultiplied", "straight"), workspace)
    checks = checks_by_id(resp)
    assert checks["video.alpha_mode_consistent"]["status"] == "PASS"  # straight constrains no pixel
    check = checks["video.alpha_edge_clean"]
    assert check["status"] == "FAIL"
    first = check["evidence"]["violations"][0]
    assert (first["width_px"], first["fringe_pixels"], first["dark_pixels"]) == (5, 5 * 36, 5 * 36)
    assert first["first"]["x"] == 17
    assert _finding(resp, "VIDEO_ALPHA_FRINGE")["message"].startswith(
        "0.000s frame 0: composited as straight, the edge shows a dark fringe 5 px wide"
    )


def test_premultiplied_file_declared_premultiplied_passes(media, workspace):
    resp = run(_edge_doc(media, "alpha_premultiplied", "premultiplied"), workspace)
    checks = checks_by_id(resp)
    assert checks["video.alpha_mode_consistent"]["status"] == "PASS"
    assert checks["video.alpha_edge_clean"]["status"] == "PASS"


def test_fringe_width_limit_is_inclusive(media, workspace):
    resp = run(_edge_doc(media, "alpha_premultiplied", "straight", max_fringe_px=5), workspace)
    assert checks_by_id(resp)["video.alpha_edge_clean"]["status"] == "PASS"


def test_listed_times_choose_the_frames(media, workspace):
    resp = run(_edge_doc(media, "alpha_straight", "straight", times=[0.5, 0.1]), workspace)
    assert [f["frame"] for f in _value(resp, "video.alpha_edges")["frames"]] == [3, 15]


def test_file_without_alpha_is_unknown(media, workspace):
    resp = run(_edge_doc(media, "clean", "straight"), workspace)
    checks = checks_by_id(resp)
    for check_id in ("video.alpha_mode_consistent", "video.alpha_edge_clean"):
        assert checks[check_id]["status"] == "UNKNOWN"
        assert checks[check_id]["reason"] == "the stream's pixel format 'yuv420p' has no alpha channel"


def test_soft_alpha_with_nothing_opaque_is_unknown_for_the_fringe(media, workspace):
    resp = run(_edge_doc(media, "alpha_glow", "straight"), workspace)
    check = checks_by_id(resp)["video.alpha_edge_clean"]
    assert check["status"] == "UNKNOWN"
    assert "no partly transparent pixel within 8 px of an opaque one" in check["reason"]


def test_frame_past_the_end_is_unknown(media, workspace):
    resp = run(_edge_doc(media, "alpha_straight", "straight", times=[0.5, 9.0]), workspace)
    check = checks_by_id(resp)["video.alpha_edge_clean"]
    assert check["status"] == "UNKNOWN" and "frame 270" in check["reason"]


# --- step 13: key / fill ----------------------------------------------------


def _kf_doc(media, fill, key, **extra):
    return _doc(media[fill], key_fill={"key_file": str(media[key]), **extra})


def test_pair_made_by_the_key_fill_export_passes(media, workspace):
    resp = run(_kf_doc(media, "kf_fill", "kf_key"), workspace)
    assert checks_by_id(resp)["video.key_fill_match"]["status"] == "PASS"
    pair = _value(resp, "video.key_fill_pair")
    assert pair["mismatches"] == [] and pair["fill"]["frames"] == 30 and pair["fill"]["alpha"] is False
    frames = _value(resp, "video.key_fill_frames")
    assert (frames["mode"], frames["frames_compared"], frames["differing_frames"]) == ("black_outside_key", 30, 0)
    assert resp["provenance"]["key_input"]["fingerprint"]


def test_fill_with_alpha_is_compared_with_the_key(media, workspace):
    resp = run(_kf_doc(media, "kf_source", "kf_key"), workspace)
    assert checks_by_id(resp)["video.key_fill_match"]["status"] == "PASS"
    assert _value(resp, "video.key_fill_frames")["mode"] == "alpha"


@pytest.mark.parametrize("fill, off", [
    ("kf_fill", 16),  # the box's leading edge: a column of fill lit where the late key is still 0
    ("kf_source", 32),  # both edges: the key differs from the alpha where the box arrived and where it left
])
def test_key_one_frame_late_fails_at_that_frame(media, workspace, fill, off):
    resp = run(_kf_doc(media, fill, "kf_key_late"), workspace)
    assert checks_by_id(resp)["video.key_fill_match"]["status"] == "FAIL"
    first = _value(resp, "video.key_fill_frames")["first_differing"]
    assert first["frame"] == 1 and first["off_fraction"] > 0
    finding = _finding(resp, "VIDEO_KEY_FILL_MISMATCH")
    assert finding["timestamp"] == pytest.approx(1 / 30, abs=1e-6)
    assert finding["message"].startswith(f"0.033s frame 1: {off} pixels")


def test_fill_lit_outside_the_key_fails(media, workspace):
    resp = run(_kf_doc(media, "kf_lit_fill", "kf_lit_key"), workspace)
    frames = _value(resp, "video.key_fill_frames")
    assert checks_by_id(resp)["video.key_fill_match"]["status"] == "FAIL"
    assert frames["differing_frames"] == 1
    assert frames["first_differing"] == {"frame": 10, "time": 0.333333, "off_pixels": 1, "off_fraction": round(1 / 2304, 6)}
    resp = run(_kf_doc(media, "kf_lit_fill", "kf_lit_key", max_off_fraction=0.001), workspace)
    assert checks_by_id(resp)["video.key_fill_match"]["status"] == "PASS"


@pytest.mark.parametrize("key, field, values", [
    ("kf_key_small", "height", (36, 34)),
    ("kf_key_short", "frames", (30, 29)),
    ("kf_key_tc", "timecode", (None, "10:00:00:00")),
])
def test_size_frame_count_or_timing_mismatch_fails_before_any_pixel(media, workspace, key, field, values):
    resp = run(_kf_doc(media, "kf_fill", key), workspace)
    check = checks_by_id(resp)["video.key_fill_match"]
    assert check["status"] == "FAIL"
    mismatch = check["evidence"]["pair"]["mismatches"][0]
    assert (mismatch["field"], mismatch["fill"], mismatch["key"]) == (field, *values)
    frames = _value(resp, "video.key_fill_frames")
    assert frames["measured"] is False and frames["reason"].startswith("not compared")
    assert "no frame was compared" in _finding(resp, "VIDEO_KEY_FILL_MISMATCH")["message"]


def test_untagged_full_range_key_is_read_as_limited_unless_told(media, workspace):
    resp = run(_kf_doc(media, "kf_soft_source", "kf_soft_key"), workspace)
    assert checks_by_id(resp)["video.key_fill_match"]["status"] == "FAIL"
    assert _value(resp, "video.key_fill_frames")["key_range"] == "limited"
    resp = run(_kf_doc(media, "kf_soft_source", "kf_soft_key", key_range="full"), workspace)
    assert checks_by_id(resp)["video.key_fill_match"]["status"] == "PASS"


def test_unreadable_key_is_unknown(media, workspace):
    bogus = workspace / "not_a_key.mov"
    bogus.write_text("not media", encoding="utf-8")
    resp = run(_doc(media["kf_fill"], key_fill={"key_file": str(bogus)}), workspace)
    check = checks_by_id(resp)["video.key_fill_match"]
    assert check["status"] == "UNKNOWN" and check["reason"].startswith("the key file could not be read")


def test_missing_key_is_a_request_error(media, workspace):
    with pytest.raises(QCError) as exc:
        run(_doc(media["kf_fill"], key_fill={"key_file": str(workspace / "nope.mov")}), workspace)
    assert exc.value.code == "MISSING_INPUT"


def test_key_outside_the_allowed_roots_is_refused(media, workspace):
    with pytest.raises(QCError) as exc:
        run(_kf_doc(media, "kf_fill", "kf_key"), workspace, allowed_roots=[str(workspace)])
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_identity_follows_the_key_content_not_its_path(media, workspace):
    moved = workspace / "elsewhere.mov"
    shutil.copy(media["kf_key"], moved)

    def key(path):
        doc = _doc(media["kf_fill"], key_fill={"key_file": str(path)})
        doc["cache_policy"] = "bypass"
        return run(doc, workspace)["cache"]["key"]

    assert key(media["kf_key"]) == key(moved)
    shutil.copy(media["kf_key_late"], moved)
    assert key(media["kf_key"]) != key(moved)


def test_delivery_package_checks_a_key_fill_pair(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "fill", "artifact_type": "video", "path": str(media["kf_fill"])},
            {"artifact_id": "loop", "artifact_type": "video", "path": str(media["vloop_orbit_short"])},
        ],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "fill", "video": {"key_fill": {"key_file": str(media["kf_key_late"])}}},
            {"artifact_id": "loop", "video": {"loop_seam": {"max_seam_ratio": 1.3}}},
        ]}},
    }
    resp = run(doc, workspace)
    statuses = {(c["artifact_id"], c["check_id"]): c["status"] for c in resp["report"]["checks"]}
    assert statuses[("fill", "video.key_fill_match")] == "FAIL"
    assert statuses[("loop", "video.loop_seam_within_limit")] == "FAIL"
    by_id = {a["artifact_id"]: a for a in resp["provenance"]["artifacts"]}
    assert "key_fingerprint" in by_id["fill"] and "key_fingerprint" not in by_id["loop"]


# --- shared -----------------------------------------------------------------


def test_no_material_rule_measures_nothing(media, workspace):
    resp = run({"operation": "check", "kind": "video", "input": str(media["alpha_straight"])}, workspace)
    ids = set(measurements_by_id(resp))
    assert not ids & {"video.loop_seam", "video.alpha_edges", "video.key_fill_pair", "video.key_fill_frames"}


def test_without_ffmpeg_the_material_checks_are_unknown(media, workspace):
    ctx = make_ctx(workspace)
    ctx.capabilities = dataclasses.replace(CAPS, ffmpeg_path="")
    doc = _doc(
        media["kf_fill"], loop_seam={"max_seam_ratio": 1.3}, alpha_edge={"alpha_mode": "straight", "max_fringe_px": 1},
        key_fill={"key_file": str(media["kf_key"])},
    )
    checks = checks_by_id(run_report(parse_request(doc), ctx))
    for check_id in ("video.loop_seam_within_limit", "video.alpha_mode_consistent", "video.alpha_edge_clean", "video.key_fill_match"):
        assert checks[check_id]["status"] == "UNKNOWN" and checks[check_id]["reason"] == "ffmpeg is not available"


def test_unknown_alpha_capability_is_unknown_not_a_fill_without_alpha(media, workspace, monkeypatch):
    from qc_skill.measurements import key_fill as key_fill_module

    monkeypatch.setattr(key_fill_module, "alpha_pixel_formats", lambda _path: None)
    resp = run(_kf_doc(media, "kf_source", "kf_key_late"), workspace)
    check = checks_by_id(resp)["video.key_fill_match"]
    assert check["status"] == "UNKNOWN" and "whether the fill carries alpha" in check["reason"]


def test_one_off_pixel_in_a_large_frame_still_differs():
    from qc_skill.measurements.key_fill import _off_pixels

    pixels = 1920 * 1080
    key = bytes(pixels)
    fill = bytes(pixels - 1) + b"\x80"
    off = _off_pixels(key, fill, "black_outside_key", 2)
    assert off == 1 and round(off / pixels, 6) == 0.0 and off > 0.0 * pixels
