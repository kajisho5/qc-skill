"""Loop seams for audio (ADR-025): click, level and spectral shape across
the wrap (end -> start) and across listed internal seams.

Fixtures (tests/fixtures/generate.py, 16 kHz 16-bit PCM):
``loop_seamless.wav`` is 2 s of 250 Hz + 1000 Hz (whole cycles, so it wraps
continuously); ``loop_naive.wav`` is the same cut at 2.003 s (a jump at the
wrap); ``loop_repeat10`` / ``loop_naive_repeat10`` repeat each ten times;
``loop_level_step`` / ``loop_spectrum_step`` are 4 s beds whose two sides
of the wrap differ in level / in balance between 250 and 2000 Hz while the
wrap itself is continuous."""

from __future__ import annotations

import cmath
import dataclasses
import json
import math

import pytest

from qc_skill.engine import run_report
from qc_skill.errors import QCError
from qc_skill.measurements.loop_seam import (
    LEVEL_FLOOR_DB, _fft, _fft_tables, band_levels, click_ratio, frame_length, rms_dbfs, spectrum_difference,
)
from qc_skill.schemas import parse_request
from tests.helpers import CAPS, checks_by_id, make_ctx, measurements_by_id, run

LIMITS = {"window_sec": 0.5, "max_click_ratio": 3, "max_level_diff_db": 1.5, "max_band_diff_db": 3}
CHECK_IDS = ("audio.loop_seam_click_within_limit", "audio.loop_seam_level_within_limit", "audio.loop_seam_spectrum_within_limit")


def _doc(path, **loop_seam):
    return {"operation": "check", "kind": "audio", "input": str(path), "rules": {"audio": {"loop_seam": loop_seam}}}


def _statuses(resp):
    checks = checks_by_id(resp)
    return tuple(checks[c]["status"] if c in checks else None for c in CHECK_IDS)


def _seams(resp):
    return measurements_by_id(resp)["audio.loop_seams"]["value"]["seams"]


# --- pure parts ---------------------------------------------------------------


def _sine(n, freq, rate, phase=0.0, amp=0.5):
    return [amp * math.sin(2 * math.pi * freq * i / rate + phase) for i in range(n)]


def test_click_ratio_is_about_one_on_a_continuous_signal_and_large_on_a_jump():
    rate = 16000
    smooth = _sine(4000, 250, rate)
    assert click_ratio(smooth, 2000, rate)["ratio"] == pytest.approx(1.0, abs=0.05)
    jumped = smooth[:2000] + _sine(2000, 250, rate, phase=math.pi)
    c = click_ratio(jumped, 2000, rate)
    assert c["ratio"] > 10 and c["from_silence"] is False


def test_click_ratio_from_and_in_silence():
    rate = 16000
    assert click_ratio([0.0] * 4000, 2000, rate) == {"ratio": 0.0, "from_silence": False, "peak": 0.0, "reference": 0.0, "step": 0.0}
    c = click_ratio([0.0] * 1999 + [0.5] + [0.0] * 2000, 2000, rate)
    assert c["ratio"] is None and c["from_silence"] is True and c["peak"] == 1.0


def test_rms_dbfs():
    assert rms_dbfs([1.0, -1.0] * 100) == 0.0
    assert rms_dbfs([0.5, -0.5] * 100) == pytest.approx(-6.0206, abs=1e-4)
    assert rms_dbfs([0.0] * 10) == LEVEL_FLOOR_DB
    assert rms_dbfs([]) == LEVEL_FLOOR_DB


def test_fft_matches_a_direct_dft():
    values = [complex(v) for v in (0.5, -1.0, 0.25, 2.0, 0.0, -0.75, 1.5, 0.125)]
    rev, twiddles, _ = _fft_tables(8)
    fast = _fft(values, rev, twiddles)
    for k in range(8):
        direct = sum(values[n] * cmath.exp(-2j * math.pi * k * n / 8) for n in range(8))
        assert fast[k] == pytest.approx(direct)


def test_frame_length_is_a_tenth_of_a_second_or_the_window():
    assert frame_length(16000, 8000) == 1024
    assert frame_length(48000, 48000) == 4096
    assert frame_length(44100, 44100) == 4096
    assert frame_length(48000, 1000) == 512


def test_band_levels_put_a_tone_in_its_octave():
    levels = dict(band_levels(_sine(8192, 1000, 16000), 16000, 1024))
    assert max(levels, key=levels.get) == 1000.0
    assert levels[1000.0] == pytest.approx(10 * math.log10(0.125), abs=0.5)  # mean square of a 0.5 sine


def test_spectrum_difference_compares_shape_not_level():
    rate = 16000
    loud, quiet = _sine(8000, 500, rate, amp=0.5), _sine(8000, 500, rate, amp=0.05)
    assert spectrum_difference(loud, quiet, rate)["max_diff_db"] == pytest.approx(0.0, abs=0.01)
    other = _sine(8000, 4000, rate)
    assert spectrum_difference(loud, other, rate)["max_diff_db"] > 20
    silent = spectrum_difference(loud, [0.0] * 8000, rate)
    assert silent["max_diff_db"] is None and silent["silent_side"] == "after"
    assert spectrum_difference([0.0] * 8000, [0.0] * 8000, rate)["max_diff_db"] == 0.0


# --- request schema ---------------------------------------------------------


@pytest.mark.parametrize("loop_seam", [
    {"window_sec": 0},
    {"window_sec": -1},
    {"window_sec": 61},
    {"window_sec": "1"},
    {"seams": 2.0},
    {"seams": [2.0, 2.0]},
    {"seams": [-1.0]},
    {"include_wrap": "yes"},
    {"include_wrap": False},
    {"include_wrap": False, "seams": []},
    {"max_level_diff_db": 1.5},
    {"max_band_diff_db": 6},
    {"max_click_ratio": -1},
    {"max_click": 3},
])
def test_invalid_loop_seam_rules_are_refused(loop_seam):
    with pytest.raises(QCError) as exc:
        parse_request(_doc("x.wav", **loop_seam))
    assert exc.value.code == "INVALID_REQUEST"


def test_loop_seam_rule_parses_with_no_default_limits():
    rule = parse_request(_doc("x.wav", seams=[4, 2.0], max_click_ratio=3)).audio_rule.loop_seam
    assert rule.seams == [2.0, 4.0] and rule.include_wrap is True and rule.window_sec is None
    assert (rule.max_level_diff_db, rule.max_band_diff_db) == (None, None)


# --- real media ---------------------------------------------------------------


def test_seamless_loop_passes_every_check(media, workspace):
    resp = run(_doc(media["loop_seamless"], **LIMITS), workspace)
    assert _statuses(resp) == ("PASS", "PASS", "PASS")
    value = measurements_by_id(resp)["audio.loop_seams"]["value"]
    assert (value["sample_rate"], value["channels"], value["samples"]) == (16000, 1, 32000)
    wrap = value["seams"][0]
    assert wrap["seam"] == "wrap" and wrap["time_sec"] == 2.0
    assert wrap["click"]["ratio"] < 1.5 and wrap["level"]["diff_db"] < 0.1 and wrap["spectrum"]["max_diff_db"] < 0.5
    json.dumps(resp)


def test_naive_loop_fails_with_the_click_in_seconds(media, workspace):
    resp = run(_doc(media["loop_naive"], **LIMITS), workspace)
    assert _statuses(resp) == ("FAIL", "PASS", "PASS")
    finding = next(f for f in resp["report"]["findings"] if f["code"] == "AUDIO_LOOP_SEAM_CLICK")
    assert finding["timestamp"] == 2.003
    violation = finding["evidence"]["violations"][0]
    assert violation["seam"] == "wrap" and violation["ratio"] > 3 and violation["step"] > 0.1
    assert violation["description"].startswith("2.003s wrap (end -> start): click ratio")


def test_bed_repeated_ten_times_passes_at_every_seam(media, workspace):
    seams = [2.0 * k for k in range(1, 10)]
    resp = run(_doc(media["loop_repeat10"], seams=seams, window_sec=1.0, max_click_ratio=3, max_level_diff_db=1.5, max_band_diff_db=3), workspace)
    assert _statuses(resp) == ("PASS", "PASS", "PASS")
    assert [s["seam"] for s in _seams(resp)] == ["wrap"] + seams
    assert all(s["click"]["measured"] and s["level"]["measured"] and s["spectrum"]["measured"] for s in _seams(resp))


def test_naive_cut_repeated_ten_times_fails_at_every_seam(media, workspace):
    seams = [round(2.003 * k, 3) for k in range(1, 10)]
    resp = run(_doc(media["loop_naive_repeat10"], seams=seams, max_click_ratio=3), workspace)
    check = checks_by_id(resp)["audio.loop_seam_click_within_limit"]
    assert check["status"] == "FAIL"
    assert [v["time_sec"] for v in check["evidence"]["violations"]] == seams + [20.03]  # sorted by time, the wrap last


def test_level_step_fails_only_the_level_check(media, workspace):
    resp = run(_doc(media["loop_level_step"], **LIMITS), workspace)
    assert _statuses(resp) == ("PASS", "FAIL", "PASS")
    level = _seams(resp)[0]["level"]
    assert level["before_dbfs"] < level["after_dbfs"] and level["diff_db"] > 3


def test_spectrum_step_fails_only_the_spectrum_check(media, workspace):
    resp = run(_doc(media["loop_spectrum_step"], **LIMITS), workspace)
    assert _statuses(resp) == ("PASS", "PASS", "FAIL")
    finding = next(f for f in resp["report"]["findings"] if f["code"] == "AUDIO_LOOP_SEAM_SPECTRUM_MISMATCH")
    assert "octave band" in finding["evidence"]["violations"][0]["description"]


@pytest.mark.parametrize("fixture, field, fact, key", [
    ("loop_naive", "max_click_ratio", "click", "ratio"),
    ("loop_level_step", "max_level_diff_db", "level", "diff_db"),
    ("loop_spectrum_step", "max_band_diff_db", "spectrum", "max_diff_db"),
])
def test_each_limit_is_exact_at_its_edge(media, workspace, fixture, field, fact, key):
    measured = _seams(run(_doc(media[fixture], window_sec=0.5), workspace))[0][fact][key]
    check_id = {"click": CHECK_IDS[0], "level": CHECK_IDS[1], "spectrum": CHECK_IDS[2]}[fact]
    at_limit = run(_doc(media[fixture], window_sec=0.5, **{field: measured}), workspace)
    assert checks_by_id(at_limit)[check_id]["status"] == "PASS"
    below = run(_doc(media[fixture], window_sec=0.5, **{field: round(measured - 0.0001, 4)}), workspace)
    assert checks_by_id(below)[check_id]["status"] == "FAIL"


def test_measurement_without_limits_runs_no_check(media, workspace):
    resp = run(_doc(media["loop_naive"], window_sec=0.5), workspace)
    assert _statuses(resp) == (None, None, None)
    assert _seams(resp)[0]["click"]["ratio"] > 3


def test_digital_silence_passes(media, workspace):
    resp = run(_doc(media["loop_silence"], **LIMITS), workspace)
    assert _statuses(resp) == ("PASS", "PASS", "PASS")
    wrap = _seams(resp)[0]
    assert (wrap["click"]["ratio"], wrap["level"]["before_dbfs"], wrap["spectrum"]["bands_compared"]) == (0.0, LEVEL_FLOOR_DB, 0)


def test_click_out_of_silence_fails(media, workspace):
    resp = run(_doc(media["loop_click_from_silence"], max_click_ratio=3), workspace)
    check = checks_by_id(resp)["audio.loop_seam_click_within_limit"]
    assert check["status"] == "FAIL"
    violation = check["evidence"]["violations"][0]
    assert violation["ratio"] is None and violation["from_silence"] is True
    assert "a click out of digital silence" in violation["description"]


def test_a_click_on_one_channel_is_found(media, workspace):
    resp = run(_doc(media["loop_stereo_click"], seams=[0.5, 1.0], max_click_ratio=3), workspace)
    check = checks_by_id(resp)["audio.loop_seam_click_within_limit"]
    assert check["status"] == "FAIL"
    assert [(v["seam"], v["channel"]) for v in check["evidence"]["violations"]] == [(1.0, 1), ("wrap", 1)]
    assert check["evidence"]["worst"]["seam"] in (1.0, "wrap")


def test_windows_that_do_not_fit_are_unknown(media, workspace):
    resp = run(_doc(media["loop_seamless"], window_sec=1.5, max_click_ratio=3, max_level_diff_db=1.5, max_band_diff_db=3), workspace)
    assert _statuses(resp) == ("PASS", "UNKNOWN", "UNKNOWN")
    assert "the file is shorter than two windows" in checks_by_id(resp)[CHECK_IDS[1]]["reason"]


def test_a_seam_past_the_end_is_unknown_unless_another_fails(media, workspace):
    resp = run(_doc(media["loop_seamless"], seams=[5.0], max_click_ratio=3), workspace)
    check = checks_by_id(resp)["audio.loop_seam_click_within_limit"]
    assert check["status"] == "UNKNOWN" and "past the end" in check["reason"]
    resp = run(_doc(media["loop_naive"], seams=[5.0], max_click_ratio=3), workspace)
    assert checks_by_id(resp)["audio.loop_seam_click_within_limit"]["status"] == "FAIL"


def test_a_seam_at_the_edge_has_no_click_context(media, workspace):
    resp = run(_doc(media["loop_seamless"], include_wrap=False, seams=[0.01], max_click_ratio=3), workspace)
    check = checks_by_id(resp)["audio.loop_seam_click_within_limit"]
    assert check["status"] == "UNKNOWN" and "50 ms on both sides" in check["reason"]


def test_audio_with_no_samples_is_unknown(media, workspace):
    resp = run(_doc(media["loop_empty"], **LIMITS), workspace)
    assert _statuses(resp) == ("UNKNOWN", "UNKNOWN", "UNKNOWN")
    assert checks_by_id(resp)[CHECK_IDS[0]]["reason"].startswith("the audio could not be decoded")


def test_no_loop_seam_rule_measures_nothing(media, workspace):
    resp = run({"operation": "check", "kind": "audio", "input": str(media["loop_naive"])}, workspace)
    assert "audio.loop_seams" not in measurements_by_id(resp)


def test_without_ffmpeg_the_loop_seam_checks_are_unknown(media, workspace):
    ctx = make_ctx(workspace)
    ctx.capabilities = dataclasses.replace(CAPS, ffmpeg_path="")
    resp = run_report(parse_request(_doc(media["loop_naive"], **LIMITS)), ctx)
    assert _statuses(resp) == ("UNKNOWN", "UNKNOWN", "UNKNOWN")
    assert checks_by_id(resp)[CHECK_IDS[0]]["reason"] == "ffmpeg is not available"


def test_loop_seam_in_a_delivery_and_a_delivery_package(media, workspace):
    doc = {"operation": "check", "kind": "delivery", "input": str(media["clean"]),
           "rules": {"delivery": {"audio": {"loop_seam": {"max_click_ratio": 1000}}}}}
    resp = run(doc, workspace)
    assert checks_by_id(resp)[CHECK_IDS[0]]["status"] == "PASS"
    assert measurements_by_id(resp)["audio.loop_seams"]["value"]["measured"] is True

    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "bed", "artifact_type": "audio", "path": str(media["loop_seamless"])},
            {"artifact_id": "naive", "artifact_type": "audio", "path": str(media["loop_naive"])},
        ],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "bed", "audio": {"loop_seam": {"max_click_ratio": 3}}},
            {"artifact_id": "naive", "audio": {"loop_seam": {"max_click_ratio": 3}}},
        ]}},
    }
    resp = run(doc, workspace)
    by_artifact = {c["artifact_id"]: c["status"] for c in resp["report"]["checks"] if c["check_id"] == CHECK_IDS[0]}
    assert by_artifact == {"bed": "PASS", "naive": "FAIL"}


def _float_wav(path, samples, rate=16000):
    """A mono 32-bit float WAV (format 3), written by hand: the wave module writes integer PCM only."""

    import struct

    data = struct.pack(f"<{len(samples)}f", *samples)
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_non_finite_samples_at_the_seam_are_unknown(workspace):
    samples = _sine(16000, 250, 16000)
    samples[-1] = float("nan")
    path = workspace / "nan_at_wrap.wav"
    _float_wav(path, samples)
    resp = run(_doc(path, **LIMITS), workspace)
    assert _statuses(resp) == ("UNKNOWN", "UNKNOWN", "UNKNOWN")
    assert "not all finite" in checks_by_id(resp)[CHECK_IDS[0]]["reason"]
    json.dumps(measurements_by_id(resp)["audio.loop_seams"], allow_nan=False)  # no non-finite value in the seam facts
