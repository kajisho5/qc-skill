"""Full-reference metrics: VMAF / SSIM / PSNR against request.reference_video (ADR-021).

Real-media E2E against ffmpeg-generated clips (tests/fixtures/generate.py):
an identical pair, an encoder-crushed copy, a half-size copy, a 30 fps
clip and a truncated clip. VMAF needs an ffmpeg built with libvmaf: where
the host's ffmpeg has it, the VMAF tests run for real; where it does not,
they are skipped and the UNKNOWN path is tested for real instead. Set
``QC_REQUIRE_LIBVMAF=1`` (the CI job whose ffmpeg has libvmaf does) to make
a missing libvmaf fail the run instead of skipping.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from qc_skill.capabilities import listed_filter_names
from qc_skill.contract import doctor_report, skill_contract
from qc_skill.engine import run_report
from qc_skill.errors import QCError
from qc_skill.measurements.reference import build_filter_complex, parse_metric_output, plan_alignment
from qc_skill.schemas import parse_request
from tests.helpers import CAPS, checks_by_id, make_ctx, measurements_by_id, run

HAS_LIBVMAF = CAPS.filter_available("libvmaf")
REQUIRE_LIBVMAF = os.environ.get("QC_REQUIRE_LIBVMAF") == "1"


def _needs_libvmaf():
    if not HAS_LIBVMAF:
        if REQUIRE_LIBVMAF:
            pytest.fail("QC_REQUIRE_LIBVMAF=1 but this ffmpeg has no libvmaf filter")
        pytest.skip("this ffmpeg has no libvmaf filter (the UNKNOWN path is tested instead)")


def _doc(media, distorted, reference, metrics, kind="video", **rule):
    video_rule = {"reference_metrics": metrics, **rule}
    rules = {"video": video_rule} if kind == "video" else {"delivery": {"video": video_rule}}
    doc = {"operation": "check", "kind": kind, "input": str(media[distorted]), "rules": rules}
    if reference is not None:
        doc["reference_video"] = str(media[reference])
    return doc


# --- SSIM / PSNR: always available --------------------------------------------


def test_identical_input_passes_ssim_and_psnr(media, workspace):
    resp = run(_doc(media, "ref_master", "ref_master", ["ssim", "psnr"]), workspace)
    checks = checks_by_id(resp)
    assert checks["video.ssim_meets_minimum"]["status"] == "PASS"
    assert checks["video.psnr_meets_minimum"]["status"] == "PASS"
    m = measurements_by_id(resp)
    assert m["video.ssim"]["value"] == 1.0
    # bit-identical: infinite PSNR is reported as libvmaf's 8-bit cap
    assert m["video.psnr_db"]["value"] == 60.0
    alignment = m["video.reference_alignment"]["value"]
    assert alignment["status"] == "aligned"
    assert alignment["psnr_capped"] is True
    assert alignment["scaled_distorted_to"] is None
    assert checks["video.ssim_meets_minimum"]["evidence"]["minimum"] == 0.95
    assert checks["video.psnr_meets_minimum"]["evidence"]["minimum"] == 31.0
    assert resp["report"]["provenance"]["reference_video_input"]["fingerprint"]
    assert resp["report"]["overall_status"] == "PASS"


def test_degraded_input_fails_ssim_and_psnr_at_the_default_thresholds(media, workspace):
    resp = run(_doc(media, "ref_degraded", "ref_master", ["ssim", "psnr"]), workspace)
    checks = checks_by_id(resp)
    assert checks["video.ssim_meets_minimum"]["status"] == "FAIL"
    assert checks["video.psnr_meets_minimum"]["status"] == "FAIL"
    m = measurements_by_id(resp)
    assert 0.3 < m["video.ssim"]["value"] < 0.95
    assert 10.0 < m["video.psnr_db"]["value"] < 31.0
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert {"VIDEO_SSIM_BELOW_MINIMUM", "VIDEO_PSNR_BELOW_MINIMUM"} <= codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_thresholds_are_configurable(media, workspace):
    resp = run(_doc(media, "ref_degraded", "ref_master", ["ssim", "psnr"], min_ssim=0.3, min_psnr_db=10.0), workspace)
    checks = checks_by_id(resp)
    assert checks["video.ssim_meets_minimum"]["status"] == "PASS"
    assert checks["video.psnr_meets_minimum"]["status"] == "PASS"
    assert checks["video.ssim_meets_minimum"]["evidence"]["minimum"] == 0.3


def test_delivery_kind_takes_reference_metrics_from_its_video_rule(media, workspace):
    resp = run(_doc(media, "ref_degraded", "ref_master", ["ssim"], kind="delivery"), workspace)
    assert checks_by_id(resp)["video.ssim_meets_minimum"]["status"] == "FAIL"


# --- VMAF ---------------------------------------------------------------------


def test_identical_input_passes_vmaf(media, workspace):
    _needs_libvmaf()
    resp = run(_doc(media, "ref_master", "ref_master", ["vmaf"]), workspace)
    check = checks_by_id(resp)["video.vmaf_meets_minimum"]
    assert check["status"] == "PASS", check
    assert measurements_by_id(resp)["video.vmaf"]["value"] >= 93.0
    assert check["evidence"]["minimum"] == 93.0


def test_degraded_input_fails_vmaf(media, workspace):
    _needs_libvmaf()
    resp = run(_doc(media, "ref_degraded", "ref_master", ["vmaf", "ssim", "psnr"]), workspace)
    checks = checks_by_id(resp)
    assert checks["video.vmaf_meets_minimum"]["status"] == "FAIL"
    assert measurements_by_id(resp)["video.vmaf"]["value"] < 93.0
    assert "VIDEO_VMAF_BELOW_MINIMUM" in {f["code"] for f in resp["report"]["findings"]}


def test_vmaf_is_unknown_on_an_ffmpeg_without_libvmaf(media, workspace):
    if HAS_LIBVMAF:
        pytest.skip("this ffmpeg has libvmaf (the simulated-missing test covers this path)")
    _assert_vmaf_unknown_ssim_psnr_pass(run(_doc(media, "ref_master", "ref_master", ["vmaf", "ssim", "psnr"]), workspace))


def test_vmaf_is_unknown_when_libvmaf_is_missing_simulated(media, workspace):
    caps = replace(CAPS, filters={**CAPS.filters, "libvmaf": "missing"})
    ctx = replace(make_ctx(workspace), capabilities=caps)
    resp = run_report(parse_request(_doc(media, "ref_master", "ref_master", ["vmaf", "ssim", "psnr"])), ctx)
    _assert_vmaf_unknown_ssim_psnr_pass(resp)


def test_libvmaf_failing_at_run_time_does_not_take_ssim_psnr_down(media, workspace):
    if HAS_LIBVMAF:
        pytest.skip("needs an ffmpeg without libvmaf to make the libvmaf filter fail for real")
    caps = replace(CAPS, filters={**CAPS.filters, "libvmaf": "available"})  # claims libvmaf, ffmpeg refuses it
    ctx = replace(make_ctx(workspace), capabilities=caps)
    resp = run_report(parse_request(_doc(media, "ref_master", "ref_master", ["vmaf", "ssim", "psnr"])), ctx)
    checks = checks_by_id(resp)
    assert checks["video.vmaf_meets_minimum"]["status"] == "UNKNOWN"
    assert "failed at run time" in checks["video.vmaf_meets_minimum"]["reason"]
    assert checks["video.ssim_meets_minimum"]["status"] == "PASS"
    assert checks["video.psnr_meets_minimum"]["status"] == "PASS"


def _assert_vmaf_unknown_ssim_psnr_pass(resp):
    checks = checks_by_id(resp)
    vmaf = checks["video.vmaf_meets_minimum"]
    assert vmaf["status"] == "UNKNOWN"
    assert "libvmaf" in vmaf["reason"]
    assert measurements_by_id(resp)["video.vmaf"]["value"] is None
    assert checks["video.ssim_meets_minimum"]["status"] == "PASS"
    assert checks["video.psnr_meets_minimum"]["status"] == "PASS"
    assert not resp["report"]["findings"]
    assert resp["report"]["overall_status"] == "UNKNOWN"  # never collapsed into PASS or FAIL


# --- alignment / mismatch -----------------------------------------------------


def test_size_mismatch_scales_the_distorted_side_to_the_reference(media, workspace):
    resp = run(_doc(media, "ref_small", "ref_master", ["ssim", "psnr"]), workspace)
    alignment = measurements_by_id(resp)["video.reference_alignment"]["value"]
    assert alignment["status"] == "aligned"
    assert alignment["scaled_distorted_to"] == [320, 240]
    assert alignment["compared_size"] == [320, 240]
    ssim = measurements_by_id(resp)["video.ssim"]["value"]
    assert 0.5 < ssim < 1.0


def test_frame_rate_mismatch_is_refused_not_scored(media, workspace):
    resp = run(_doc(media, "ref_30fps", "ref_master", ["ssim", "psnr"]), workspace)
    m = measurements_by_id(resp)
    alignment = m["video.reference_alignment"]["value"]
    assert alignment["status"] == "refused"
    assert alignment["code"] == "FRAME_RATE_MISMATCH"
    assert alignment["distorted"]["frame_rate"] == 30.0
    assert alignment["reference"]["frame_rate"] == 25.0
    assert m["video.ssim"]["value"] is None
    checks = checks_by_id(resp)
    for check_id in ("video.ssim_meets_minimum", "video.psnr_meets_minimum"):
        assert checks[check_id]["status"] == "UNKNOWN"
        assert "FRAME_RATE_MISMATCH" in checks[check_id]["reason"]
        assert checks[check_id]["evidence"]["alignment"]["code"] == "FRAME_RATE_MISMATCH"
    assert not resp["report"]["findings"]
    assert resp["report"]["overall_status"] == "UNKNOWN"


def test_frame_count_mismatch_is_refused(media, workspace):
    resp = run(_doc(media, "ref_short", "ref_master", ["psnr"]), workspace)
    alignment = measurements_by_id(resp)["video.reference_alignment"]["value"]
    assert alignment["code"] == "FRAME_COUNT_MISMATCH"
    assert checks_by_id(resp)["video.psnr_meets_minimum"]["status"] == "UNKNOWN"


def test_missing_reference_video_is_unknown(media, workspace):
    resp = run(_doc(media, "ref_master", None, ["ssim"]), workspace)
    check = checks_by_id(resp)["video.ssim_meets_minimum"]
    assert check["status"] == "UNKNOWN"
    assert "reference_video" in check["reason"]


def test_plan_alignment_rules():
    base = {"width": 320, "height": 240, "frame_rate": 25.0, "frame_count": 50, "duration_sec": 2.0, "pix_fmt": "yuv420p"}
    assert plan_alignment(base, base)["status"] == "aligned"
    assert plan_alignment({**base, "frame_count": 51}, base)["status"] == "aligned"  # one frame of slack
    assert plan_alignment({**base, "frame_count": 52}, base)["code"] == "FRAME_COUNT_MISMATCH"
    assert plan_alignment({**base, "frame_rate": 25.02}, base)["code"] == "FRAME_RATE_MISMATCH"
    assert plan_alignment({**base, "frame_rate": None}, base)["code"] == "FRAME_RATE_UNKNOWN"
    assert plan_alignment(base, {**base, "width": None})["code"] == "REFERENCE_NO_VIDEO"
    no_counts = {**base, "frame_count": None}
    assert plan_alignment(no_counts, {**no_counts, "duration_sec": 2.03})["status"] == "aligned"
    assert plan_alignment(no_counts, {**no_counts, "duration_sec": 3.0})["code"] == "DURATION_MISMATCH"
    rgb_ref = plan_alignment(base, {**base, "pix_fmt": "rgb24"})
    assert rgb_ref["pix_fmt"] == "yuv420p"
    assert plan_alignment(base, {**base, "pix_fmt": "yuv420p10le"})["pix_fmt"] == "yuv420p10le"


def test_filter_graph_is_built_from_fixed_parts_only():
    base = {"width": 160, "height": 120, "frame_rate": 25.0, "frame_count": 50, "duration_sec": 2.0, "pix_fmt": "yuv420p"}
    alignment = plan_alignment(base, {**base, "width": 320, "height": 240})
    graph, outputs = build_filter_complex(alignment, ["vmaf", "ssim", "psnr"], 0, 0)
    assert graph.startswith("[0:0]setpts=PTS-STARTPTS,scale=320:240:flags=bicubic,format=yuv420p,split=3[d0][d1][d2]")
    assert "[d0][r0]libvmaf=shortest=1[o0]" in graph
    assert outputs == ["[o0]", "[o1]", "[o2]"]


def test_parse_metric_output():
    stderr = (
        "[Parsed_ssim_7 @ 0x1] SSIM Y:0.823582 (7.534572) U:0.852287 (8.305816) V:0.870274 (8.869744) All:0.836148 (7.855490)\n"
        "[Parsed_psnr_8 @ 0x2] PSNR y:25.809084 u:26.785756 v:23.852630 average:25.550779 min:25.148419 max:26.115907\n"
        "[Parsed_libvmaf_9 @ 0x3] VMAF score: 41.123456\n"
    )
    assert parse_metric_output(stderr) == {"ssim": 0.836148, "psnr": "25.550779", "vmaf": 41.123456}
    assert parse_metric_output("[Parsed_psnr_0 @ 0x2] PSNR y:inf u:inf v:inf average:inf min:inf max:inf")["psnr"] == "inf"


# --- opt-in only: defaults unchanged --------------------------------------------


def test_reference_video_is_ignored_for_kind_video_without_reference_metrics(media, workspace):
    base = {"operation": "check", "kind": "video", "input": str(media["ref_master"])}
    plain = run(base, workspace)
    with_ref = run({**base, "reference_video": str(media["ref_degraded"])}, workspace)
    assert plain["provenance"]["identity"] == with_ref["provenance"]["identity"]
    assert "reference_video_input" not in with_ref["provenance"]
    ids = set(measurements_by_id(with_ref))
    assert not ids & {"video.reference_alignment", "video.vmaf", "video.ssim", "video.psnr_db"}
    assert not set(checks_by_id(with_ref)) & {"video.vmaf_meets_minimum", "video.ssim_meets_minimum", "video.psnr_meets_minimum"}


@pytest.mark.parametrize("rule", [
    {"reference_metrics": []},
    {"reference_metrics": ["vmaf", "ms_ssim"]},
    {"reference_metrics": ["ssim", "ssim"]},
    {"reference_metrics": "vmaf"},
    {"reference_metrics": ["ssim"], "min_ssim": 1.5},
    {"reference_metrics": ["vmaf"], "min_vmaf": 101},
    {"reference_metrics": ["psnr"], "min_psnr_db": -1},
    {"reference_metrics": ["psnr"], "min_psnr_db": True},
])
def test_invalid_reference_metric_rules_are_rejected(rule):
    with pytest.raises(QCError) as exc:
        parse_request({"operation": "check", "kind": "video", "input": "x.mp4", "rules": {"video": rule}})
    assert exc.value.code == "INVALID_REQUEST"


def test_reference_metrics_are_stored_in_canonical_order():
    req = parse_request({"operation": "check", "kind": "video", "input": "x.mp4", "rules": {"video": {"reference_metrics": ["psnr", "vmaf"]}}})
    assert req.video_rule.reference_metrics == ["vmaf", "psnr"]


def test_rule_types_stay_within_the_typed_rule_grammar():
    video = skill_contract()["rules"]["video"]
    assert video["reference_metrics"] == {"type": "Optional[List[str]]", "default": None}
    assert video["min_vmaf"] == {"type": "float", "default": 93.0}
    assert video["min_ssim"] == {"type": "float", "default": 0.95}
    assert video["min_psnr_db"] == {"type": "float", "default": 31.0}


# --- capability detection / doctor --------------------------------------------


def test_filter_names_are_matched_exactly():
    listing = (
        "Filters:\n  T.. = Timeline support\n  ------\n"
        " ... libvmaf_cuda      VV->V      Calculate the VMAF between two video streams.\n"
        " TS. ssim360           VV->V      Calculate the SSIM between two 360 video streams.\n"
        " TS. psnr              VV->V      Calculate the PSNR between two video streams.\n"
        " ... vmafmotion        V->V       Calculate the VMAF Motion score.\n"
    )
    names = listed_filter_names(listing)
    assert "psnr" in names
    assert "libvmaf" not in names and "ssim" not in names


def test_doctor_reports_vmaf_availability(tmp_path):
    report = doctor_report(str(tmp_path))
    entry = report["checks"]["filter:libvmaf"]
    assert entry["status"] == ("AVAILABLE" if HAS_LIBVMAF else "MISSING")
    assert entry["optional"] is True
    assert entry["buildconf_enable_libvmaf"] in (True, False, None)
    assert report["checks"]["filter:ssim"]["status"] == "AVAILABLE"
    assert report["checks"]["filter:psnr"]["status"] == "AVAILABLE"
    assert ("video.vmaf_meets_minimum" in report["unavailable_tools"]) is (not HAS_LIBVMAF)
    if not HAS_LIBVMAF:
        assert "detail" in entry
        assert report["status"] == "ok"  # an optional filter never degrades doctor
