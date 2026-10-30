"""Photosensitive flash check, ITU-R BT.1702-3 general flash (ADR-016).

Real-media E2E against geq-generated clips whose luminance per frame and
per pixel is exact (tests/fixtures/generate.py), plus unit tests of the
pure flash counter.
"""

import pytest

from qc_skill.measurements.temporal import FLASH_GRID, RED_FLASH_NOTE, analyze_flashes
from tests.helpers import checks_by_id, measurements_by_id, run


def _check(media, workspace, name, **rule):
    doc = {
        "operation": "check", "kind": "video", "input": str(media[name]),
        "rules": {"video": {"check_photosensitivity": True, **rule}},
    }
    resp = run(doc, workspace)
    return resp, checks_by_id(resp)["video.photosensitive_flash_within_limit"]


def test_full_screen_10hz_flash_fails(media, workspace):
    resp, check = _check(media, workspace, "flash_10hz")
    assert check["status"] == "FAIL"
    worst = measurements_by_id(resp)["video.flash_worst_window"]["value"]
    assert worst["flashes"] == 10.0
    assert worst["changes"] == 20
    assert worst["area_fraction"] == 1.0
    finding = next(f for f in resp["report"]["findings"] if f["code"] == "VIDEO_FLASH_RISK")
    assert finding["severity"] == "FAIL"
    assert finding["evidence"]["max_flashes_per_second"] == 3.0
    assert resp["report"]["overall_status"] == "FAIL"


def test_full_screen_2hz_flash_passes(media, workspace):
    resp, check = _check(media, workspace, "flash_2hz")
    assert check["status"] == "PASS"
    assert measurements_by_id(resp)["video.flash_worst_window"]["value"]["flashes"] == 2.0


def test_small_area_flash_passes(media, workspace):
    # 9% of the screen flashing at 10 Hz: below BT.1702's 25% area condition.
    resp, check = _check(media, workspace, "flash_small_area")
    assert check["status"] == "PASS"
    assert measurements_by_id(resp)["video.flash_worst_window"]["value"]["flashes"] == 0.0


def test_half_screen_flash_fails(media, workspace):
    resp, check = _check(media, workspace, "flash_half_area")
    assert check["status"] == "FAIL"
    worst = measurements_by_id(resp)["video.flash_worst_window"]["value"]
    assert worst["flashes"] == 10.0
    assert 0.45 <= worst["area_fraction"] <= 0.55


def test_flash_below_20_cd_m2_is_not_a_flash(media, workspace):
    resp, check = _check(media, workspace, "flash_dim")
    assert check["status"] == "PASS"
    assert measurements_by_id(resp)["video.flash_worst_window"]["value"]["changes"] == 0


def test_flash_with_darker_side_above_160_cd_m2_is_not_an_sdr_flash(media, workspace):
    resp, check = _check(media, workspace, "flash_bright_only")
    assert check["status"] == "PASS"


def test_caller_can_tighten_the_limit(media, workspace):
    _, check = _check(media, workspace, "flash_2hz", max_flashes_per_second=1)
    assert check["status"] == "FAIL"


def test_area_fraction_is_a_detection_parameter(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["flash_small_area"]),
        "parameters": {"flash_area_fraction": 0.05},
        "rules": {"video": {"check_photosensitivity": True}},
    }
    assert checks_by_id(run(doc, workspace))["video.photosensitive_flash_within_limit"]["status"] == "FAIL"


def test_per_second_counts_and_red_flash_unknown(media, workspace):
    resp, _ = _check(media, workspace, "flash_10hz")
    m = measurements_by_id(resp)
    per_second = m["video.flash_per_second"]["value"]
    assert [round(s["start"]) for s in per_second] == [0, 1, 2]
    assert all(s["flashes"] >= 9.5 for s in per_second)
    assert m["video.red_flash_count"]["value"] is None
    assert m["video.red_flash_count"]["notes"] == RED_FLASH_NOTE
    assert "200 * V^2.4" in m["video.flash_per_second"]["notes"]


def test_not_measured_or_checked_unless_requested(media, workspace):
    doc = {"operation": "check", "kind": "video", "input": str(media["flash_10hz"])}
    resp = run(doc, workspace)
    assert "video.flash_worst_window" not in measurements_by_id(resp)
    assert "video.photosensitive_flash_within_limit" not in checks_by_id(resp)


def test_flash_check_runs_inside_delivery_package_video_rule(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main", "artifact_type": "video", "path": str(media["flash_10hz"])}],
        "rules": {"delivery_package": {"artifacts": [{"artifact_id": "main", "video": {"check_photosensitivity": True}}]}},
    }
    resp = run(doc, workspace)
    assert any(f["code"] == "VIDEO_FLASH_RISK" for f in resp["report"]["findings"])


# --- pure counter --------------------------------------------------------


def _frames(values):
    return [bytes([v]) * (FLASH_GRID * FLASH_GRID) for v in values]


def test_counter_counts_opposing_change_pairs():
    # 25 fps, white/black toggling every 5 frames = 2.5 Hz -> 5 changes per second
    values = [235 if (i // 5) % 2 == 0 else 16 for i in range(50)]
    out = analyze_flashes(_frames(values), [i / 25 for i in range(50)], 0.25)
    assert out["worst_window"]["changes"] == 5
    assert out["worst_window"]["flashes"] == 2.5


def test_counter_seven_changes_is_more_than_three_flashes():
    # BT.1702: "more than three flashes (i.e. six changes)" - 7 changes = 3.5 flashes
    values = [235 if (i // 3) % 2 == 0 else 16 for i in range(25)]
    out = analyze_flashes(_frames(values[:22]), [i / 25 for i in range(22)], 0.25)
    assert out["worst_window"]["changes"] == 7
    assert out["worst_window"]["flashes"] > 3


def test_counter_gradual_ramp_is_one_change():
    values = list(range(16, 236, 10)) + [235] * 10
    out = analyze_flashes(_frames(values), [i / 25 for i in range(len(values))], 0.25)
    assert out["worst_window"]["changes"] == 1


@pytest.mark.parametrize("fraction,expected", [(0.25, 0.0), (0.2, 10.0)])
def test_counter_area_threshold_is_strictly_more_than(fraction, expected):
    # exactly 25% of the cells flash: not "more than 25%"
    n = FLASH_GRID * FLASH_GRID
    quarter = n // 4
    frames = [bytes([235 if (i % 2 == 0 and c < quarter) else 16 for c in range(n)]) for i in range(20)]
    out = analyze_flashes(frames, [i / 20 for i in range(20)], fraction)
    assert out["worst_window"]["flashes"] == pytest.approx(expected, abs=0.5)
