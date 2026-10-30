"""Text legibility at caller-named regions (ADR-018): WCAG 2.x contrast
ratio between the two Otsu classes, and ink line height scaled to a
1080 px short side. Measured facts only - no OCR.

text_boxes.mp4 is 640x360 (x3 to 1080) on #202020 with drawbox "glyph"
bars of exact size; the drawtext test renders real glyphs when this
ffmpeg build has drawtext and a default font, and is skipped otherwise.
"""

import shutil
import subprocess

import pytest

from qc_skill.errors import QCError
from qc_skill.measurements.text_legibility import measure_text_pixels
from qc_skill.schemas import parse_request
from tests.helpers import checks_by_id, measurements_by_id, run

HIGH = {"t": 1.0, "box": [40, 40, 200, 80], "role": "hook"}
LOW = {"t": 1.0, "box": [320, 40, 200, 80], "role": "cta"}
SMALL = {"t": 1.0, "box": [40, 220, 200, 40], "role": "caption"}


def _run(media, workspace, regions, **rule):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["text_boxes"]),
        "rules": {"video": {"text_regions": regions, **rule}},
    }
    resp = run(doc, workspace)
    return resp, checks_by_id(resp)["video.text_legible"], measurements_by_id(resp)["video.text_regions"]["value"]


def test_high_contrast_region_passes_with_exact_height(media, workspace):
    _, check, regions = _run(media, workspace, [HIGH])
    assert check["status"] == "PASS"
    r = regions[0]
    assert r["measured"] is True
    assert r["text_height_px"] == 40
    assert r["text_height_px_at_1080"] == 120.0
    # white on #202020: (1.0 + 0.05) / (0.0144 + 0.05) ~= 16.3
    assert 15.0 < r["contrast_ratio"] < 17.5


def test_low_contrast_region_fails(media, workspace):
    resp, check, regions = _run(media, workspace, [HIGH, LOW])
    assert check["status"] == "FAIL"
    assert regions[1]["contrast_ratio"] < 1.5
    finding = next(f for f in resp["report"]["findings"] if f["code"] == "TEXT_ILLEGIBLE")
    assert [x["index"] for x in finding["evidence"]["failing"]] == [1]


def test_aaa_threshold_is_a_rule_choice(media, workspace):
    _, check, _ = _run(media, workspace, [HIGH], min_contrast_ratio=7.0)
    assert check["status"] == "PASS"
    _, check, _ = _run(media, workspace, [HIGH], min_contrast_ratio=21.0)
    assert check["status"] == "FAIL"


def test_per_role_minimum_height(media, workspace):
    _, check, regions = _run(media, workspace, [HIGH, SMALL], min_text_height_px={"caption": 30, "hook": 100})
    assert regions[1]["text_height_px"] == 6
    assert regions[1]["text_height_px_at_1080"] == 18.0
    assert check["status"] == "FAIL"
    assert [x["index"] for x in check["evidence"]["failing"]] == [1]


def test_height_not_judged_without_a_role_minimum(media, workspace):
    _, check, _ = _run(media, workspace, [SMALL])
    assert check["status"] == "PASS"  # contrast is fine; no height opinion given


def test_box_outside_frame_is_unknown(media, workspace):
    _, check, regions = _run(media, workspace, [{"t": 1.0, "box": [600, 300, 100, 100], "role": "term"}])
    assert regions[0]["measured"] is False
    assert check["status"] == "UNKNOWN"


def test_time_past_end_is_unknown(media, workspace):
    _, check, regions = _run(media, workspace, [{"t": 30.0, "box": [40, 40, 200, 80], "role": "hook"}])
    assert regions[0]["measured"] is False
    assert check["status"] == "UNKNOWN"


def _drawtext_available() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return " drawtext " in out


def test_drawtext_glyphs(tmp_path, workspace):
    if not _drawtext_available():
        pytest.skip("this ffmpeg build has no drawtext filter")
    clip = tmp_path / "drawtext.mp4"
    vf = (
        "drawtext=text='SALE':fontsize=96:fontcolor=white:x=40:y=40,"
        "drawtext=text='SALE':fontsize=16:fontcolor=white:x=40:y=240,"
        "drawtext=text='SALE':fontsize=96:fontcolor=0x2A2A2A:x=340:y=40"
    )
    result = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x202020:s=640x360:r=25:d=2",
         "-vf", vf, "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"drawtext could not render with this build's default font: {result.stderr.strip()[:200]}")
    doc = {
        "operation": "check", "kind": "video", "input": str(clip),
        "rules": {"video": {
            "text_regions": [
                {"t": 1.0, "box": [30, 30, 300, 130], "role": "hook"},
                {"t": 1.0, "box": [30, 230, 120, 40], "role": "caption"},
                {"t": 1.0, "box": [330, 30, 300, 130], "role": "cta"},
            ],
            "min_text_height_px": {"hook": 150, "caption": 60},
        }},
    }
    resp = run(doc, workspace, allowed_roots=[str(tmp_path)])
    regions = measurements_by_id(resp)["video.text_regions"]["value"]
    # All-caps, no descenders: ink height is the cap height (~0.7 x font size).
    assert 55 <= regions[0]["text_height_px"] <= 80
    assert regions[0]["contrast_ratio"] > 10
    assert regions[1]["text_height_px"] <= 16
    assert regions[2]["contrast_ratio"] < 1.5
    check = checks_by_id(resp)["video.text_legible"]
    assert check["status"] == "FAIL"
    assert sorted(x["index"] for x in check["evidence"]["failing"]) == [1, 2]


# --- pure measurement ----------------------------------------------------


def test_pure_black_on_white_is_21_to_1():
    w, h = 10, 10
    px = bytearray()
    for y in range(h):
        for x in range(w):
            ink = 3 <= y < 7 and 2 <= x < 5
            px += bytes([0, 0, 0]) if ink else bytes([255, 255, 255])
    facts = measure_text_pixels(bytes(px), w, h)
    assert facts["contrast_ratio"] == 21.0
    assert facts["text_height_px"] == 4
    assert facts["line_heights_px"] == [4]


def test_uniform_region_has_no_text():
    facts = measure_text_pixels(bytes([128, 128, 128]) * 16, 4, 4)
    assert facts["contrast_ratio"] == 1.0
    assert facts["text_height_px"] == 0


@pytest.mark.parametrize(
    "regions,extra",
    [
        ("x", {}),
        ([{"t": 1, "box": [0, 0, 10], "role": "hook"}], {}),
        ([{"t": 1, "box": [0, 0, 10, 0], "role": "hook"}], {}),
        ([{"t": 1, "box": [0, 0, 10, 1.5], "role": "hook"}], {}),
        ([{"t": -1, "box": [0, 0, 10, 10], "role": "hook"}], {}),
        ([{"t": 1, "box": [0, 0, 10, 10], "role": "headline"}], {}),
        ([{"t": 1, "box": [0, 0, 10, 10], "role": "hook", "text": "SALE"}], {}),
        ([{"t": 1, "box": [0, 0, 10, 10], "role": "hook"}], {"min_contrast_ratio": 0.5}),
        ([{"t": 1, "box": [0, 0, 10, 10], "role": "hook"}], {"min_text_height_px": {"title": 10}}),
    ],
)
def test_invalid_text_rules_are_rejected(regions, extra):
    with pytest.raises(QCError):
        parse_request({"operation": "check", "kind": "video", "input": "x.mp4",
                       "rules": {"video": {"text_regions": regions, **extra}}})


def test_forbidden_key_inside_text_region_is_still_rejected():
    with pytest.raises(QCError) as exc:
        parse_request({"operation": "check", "kind": "video", "input": "x.mp4",
                       "rules": {"video": {"text_regions": [{"t": 1, "box": [0, 0, 1, 1], "role": "hook", "filter": "drawtext"}]}}})
    assert "forbidden" in str(exc.value)
