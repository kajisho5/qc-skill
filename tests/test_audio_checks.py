import subprocess

from tests.helpers import checks_by_id, measurements_by_id, run


def test_diagnostic_raw_ffmpeg_astats_output_on_loud_clipping(media):
    """Temporary diagnostic: dump the raw ffmpeg stderr for loud_clipping.wav
    so a CI-only failure (observed on windows-latest) can be root-caused
    from the assertion message instead of guessed at. Safe to delete once
    test_clipping_detected_on_loud_clipping_fixture is green everywhere.
    """

    result = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-v", "info",
            "-protocol_whitelist", "file", "-i", str(media["loud_clipping"]),
            "-af", "astats=metadata=0:reset=0,ebur128=peak=true,silencedetect=n=-30dB:d=0.5",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    assert "Peak level dB" in result.stderr, (
        f"returncode={result.returncode!r}\n"
        f"stdout={result.stdout!r}\n"
        f"stderr={result.stderr!r}"
    )


def test_inspect_clean_audio_measurements(media, workspace):
    doc = {"operation": "inspect", "kind": "audio", "input": str(media["clean"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)
    assert m["audio.stream_present"]["value"] is True
    assert m["audio.channels"]["value"] == 1
    assert m["audio.sample_rate"]["value"] > 0
    assert m["audio.clipping_detected"]["value"] is False
    assert m["audio.integrated_loudness_lufs"]["value"] is not None


def test_video_no_audio_reports_stream_absent(media, workspace):
    doc = {"operation": "inspect", "kind": "audio", "input": str(media["video_no_audio"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)
    assert m["audio.stream_present"]["value"] is False
    assert m["audio.stream_count"]["value"] == 0


def test_require_audio_stream_true_fails_when_absent(media, workspace):
    doc = {
        "operation": "check", "kind": "audio", "input": str(media["video_no_audio"]),
        "rules": {"audio": {"require_audio_stream": True}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.stream_present_matches_expected"]["status"] == "FAIL"
    assert resp["report"]["overall_status"] == "FAIL"


def test_require_audio_stream_false_passes_when_absent(media, workspace):
    doc = {
        "operation": "check", "kind": "audio", "input": str(media["video_no_audio"]),
        "rules": {"audio": {"require_audio_stream": False}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.stream_present_matches_expected"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_clipping_detected_on_loud_clipping_fixture(media, workspace):
    doc = {"operation": "check", "kind": "audio", "input": str(media["loud_clipping"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)
    debug = {k: v.get("value") for k, v in m.items() if k.startswith("audio.")}
    assert m["audio.clipping_detected"]["value"] is True, f"measurements: {debug}"
    checks = checks_by_id(resp)
    assert checks["audio.no_clipping"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "AUDIO_CLIPPING_DETECTED" in codes


def test_clean_audio_has_no_clipping(media, workspace):
    doc = {"operation": "check", "kind": "audio", "input": str(media["clean"])}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.no_clipping"]["status"] == "PASS"


def test_internal_silence_detected_in_gap_fixture(media, workspace):
    doc = {"operation": "inspect", "kind": "audio", "input": str(media["silence_gap"])}
    resp = run(doc, workspace)
    m = measurements_by_id(resp)
    internal = m["audio.internal_silence_segments"]["value"]
    assert len(internal) == 1
    assert 1.5 < internal[0]["duration"] <= 2.1


def test_internal_silence_check_fails_over_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "audio", "input": str(media["silence_gap"]),
        "rules": {"audio": {"max_internal_silence_sec": 0.2}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.internal_silence_within_tolerance"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "AUDIO_INTERNAL_SILENCE_EXCEEDED" in codes


def test_internal_silence_check_passes_within_tolerance(media, workspace):
    doc = {
        "operation": "check", "kind": "audio", "input": str(media["silence_gap"]),
        "rules": {"audio": {"max_internal_silence_sec": 5.0}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.internal_silence_within_tolerance"]["status"] == "PASS"


def test_loudness_target_within_tolerance_passes(media, workspace):
    doc = {"operation": "inspect", "kind": "audio", "input": str(media["clean"])}
    inspected = run(doc, workspace)
    measured_lufs = measurements_by_id(inspected)["audio.integrated_loudness_lufs"]["value"]

    doc2 = {
        "operation": "check", "kind": "audio", "input": str(media["clean"]),
        "rules": {"audio": {"integrated_loudness_target_lufs": measured_lufs, "integrated_loudness_tolerance_lu": 0.5}},
    }
    resp = run(doc2, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.integrated_loudness_within_tolerance"]["status"] == "PASS"


def test_loudness_target_out_of_tolerance_fails(media, workspace):
    doc = {
        "operation": "check", "kind": "audio", "input": str(media["clean"]),
        "rules": {"audio": {"integrated_loudness_target_lufs": -14.0, "integrated_loudness_tolerance_lu": 0.5}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.integrated_loudness_within_tolerance"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "AUDIO_LOUDNESS_OUT_OF_RANGE" in codes


def test_sample_rate_and_channel_rules(media, workspace):
    inspected = run({"operation": "inspect", "kind": "audio", "input": str(media["clean"])}, workspace)
    m = measurements_by_id(inspected)
    sr, channels = m["audio.sample_rate"]["value"], m["audio.channels"]["value"]

    ok = run(
        {"operation": "check", "kind": "audio", "input": str(media["clean"]), "rules": {"audio": {"expected_sample_rate": sr, "expected_channels": channels}}},
        workspace,
    )
    checks = checks_by_id(ok)
    assert checks["audio.sample_rate_matches_expected"]["status"] == "PASS"
    assert checks["audio.channels_match_expected"]["status"] == "PASS"

    bad = run(
        {"operation": "check", "kind": "audio", "input": str(media["clean"]), "rules": {"audio": {"expected_sample_rate": sr + 1}}},
        workspace,
    )
    assert checks_by_id(bad)["audio.sample_rate_matches_expected"]["status"] == "FAIL"


def test_decode_integrity_baseline_passes_for_clean_audio(media, workspace):
    doc = {"operation": "check", "kind": "audio", "input": str(media["clean"])}
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["audio.decodes_without_errors"]["status"] == "PASS"
