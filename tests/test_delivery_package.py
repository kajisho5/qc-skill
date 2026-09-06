"""kind: "delivery_package" - N named artifacts validated as one delivery
(Phase 1, ADR-010). Real-media E2E, not synthetic JSON only - the package
still runs actual ffprobe/ffmpeg measurement per artifact underneath.
"""

import pytest

from qc_skill.errors import QCError

from tests.helpers import run


def _measurements_for(resp, artifact_id):
    return {m["id"]: m for m in resp["report"]["measurements"] if m.get("artifact_id") == artifact_id}


def _checks_for(resp, artifact_id):
    return {c["check_id"]: c for c in resp["report"]["checks"] if c.get("artifact_id") == artifact_id}


def _write_metadata(workspace, name="metadata.json", content='{"title": "demo"}'):
    p = workspace / name
    p.write_text(content, encoding="utf-8")
    return p


def test_inspect_gathers_measurements_per_artifact_without_collision(media, workspace):
    thumb = _write_metadata(workspace, "thumb.png", "not a real png, just bytes")
    doc = {
        "operation": "inspect", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "captions", "artifact_type": "subtitle", "path": str(media["subtitle_valid"])},
            {"artifact_id": "thumb", "artifact_type": "thumbnail", "path": str(thumb)},
        ],
    }
    resp = run(doc, workspace)
    assert resp["status"] == "completed"

    video_m = _measurements_for(resp, "main_video")
    assert video_m["video.width"]["value"] == 320
    assert video_m["delivery_package.artifact_present"]["value"] is True

    sub_m = _measurements_for(resp, "captions")
    assert sub_m["subtitle.cue_count"]["value"] == 2
    assert "video.width" not in sub_m  # subtitle artifact never gets video measurements

    thumb_m = _measurements_for(resp, "thumb")
    assert thumb_m["delivery_package.artifact_present"]["value"] is True
    assert thumb_m["delivery_package.artifact_extension"]["value"] == "png"
    assert "video.width" not in thumb_m and "subtitle.cue_count" not in thumb_m


def test_two_subtitle_artifacts_do_not_collide(media, workspace):
    ja_srt = workspace / "ja.srt"
    ja_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nこんにちは\n\n", encoding="utf-8")
    doc = {
        "operation": "inspect", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "sub_en", "artifact_type": "subtitle", "path": str(media["subtitle_valid"])},
            {"artifact_id": "sub_ja", "artifact_type": "subtitle", "path": str(ja_srt)},
        ],
    }
    resp = run(doc, workspace)
    en_m = _measurements_for(resp, "sub_en")
    ja_m = _measurements_for(resp, "sub_ja")
    assert en_m["subtitle.cue_count"]["value"] == 2
    assert ja_m["subtitle.cue_count"]["value"] == 1


def test_required_artifact_present_passes(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [{"artifact_id": "main_video", "required": True}]}},
    }
    resp = run(doc, workspace)
    checks = _checks_for(resp, "main_video")
    assert checks["delivery_package.artifact_present"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_required_artifact_missing_fails(media, workspace):
    missing_path = workspace / "does_not_exist.mp4"
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(missing_path)}],
        "rules": {"delivery_package": {"artifacts": [{"artifact_id": "main_video", "required": True}]}},
    }
    resp = run(doc, workspace)
    checks = _checks_for(resp, "main_video")
    assert checks["delivery_package.artifact_present"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_PACKAGE_ARTIFACT_MISSING" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_required_artifact_never_listed_in_request_also_fails(media, workspace):
    # The rule names an artifact_id that request.artifacts never declared
    # at all - this must be indistinguishable from "not present", not a
    # KeyError or a silent skip.
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "main_video", "required": True},
            {"artifact_id": "thumbnail", "required": True},
        ]}},
    }
    resp = run(doc, workspace)
    checks = _checks_for(resp, "thumbnail")
    assert checks["delivery_package.artifact_present"]["status"] == "FAIL"


def test_optional_artifact_absent_passes(media, workspace):
    missing_path = workspace / "optional_thumb.png"
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "thumb", "artifact_type": "thumbnail", "path": str(missing_path)}],
        "rules": {"delivery_package": {"artifacts": [{"artifact_id": "thumb", "required": False}]}},
    }
    resp = run(doc, workspace)
    checks = _checks_for(resp, "thumb")
    assert checks["delivery_package.artifact_present"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_extension_mismatch_fails(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "main_video", "expected_extension": "mov"},
        ]}},
    }
    resp = run(doc, workspace)
    checks = _checks_for(resp, "main_video")
    assert checks["delivery_package.artifact_extension_matches_expected"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_PACKAGE_ARTIFACT_EXTENSION_MISMATCH" in codes


def test_min_size_fails_for_tiny_requirement_violation(media, workspace):
    size = media["clean"].stat().st_size
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "main_video", "min_size_bytes": size + 1000},
        ]}},
    }
    resp = run(doc, workspace)
    checks = _checks_for(resp, "main_video")
    assert checks["delivery_package.artifact_size_within_limit"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "DELIVERY_PACKAGE_ARTIFACT_TOO_SMALL" in codes


def test_nested_video_rule_runs_against_that_one_artifact_only(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "main_video", "video": {"expected_width": 1920, "expected_height": 1080}},
        ]}},
    }
    resp = run(doc, workspace)
    checks = _checks_for(resp, "main_video")
    assert checks["video.resolution_matches_expected"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "VIDEO_RESOLUTION_MISMATCH" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_artifact_type_mismatch_between_request_and_rule_is_rejected(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "main_video", "artifact_type": "audio"},
        ]}},
    }
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "INVALID_REQUEST"


def test_input_field_is_not_required_for_delivery_package(media, workspace):
    doc = {
        "operation": "inspect", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
    }
    resp = run(doc, workspace)
    assert resp["status"] == "completed"


def test_traversal_in_artifact_path_is_rejected_even_though_must_exist_is_false(workspace):
    doc = {
        "operation": "inspect", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "x", "artifact_type": "video", "path": "../../etc/passwd"}],
    }
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_identity_is_deterministic_across_runs(media, workspace):
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [{"artifact_id": "main_video", "required": True}]}},
    }
    workspace_a = workspace / "a"
    workspace_b = workspace / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    first = run(doc, workspace_a)
    second = run(doc, workspace_b)
    assert first["provenance"]["identity"] == second["provenance"]["identity"]
    assert first["report"]["id"] == second["report"]["id"]


def test_identity_changes_when_a_required_artifact_goes_missing(media, workspace):
    present_doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
    }
    missing_doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(workspace / "gone.mp4")}],
    }
    r1 = run(present_doc, workspace)
    r2 = run(missing_doc, workspace)
    assert r1["provenance"]["identity"] != r2["provenance"]["identity"]


def test_provenance_lists_every_artifact_with_presence_and_fingerprint(media, workspace):
    doc = {
        "operation": "inspect", "kind": "delivery_package",
        "artifacts": [
            {"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])},
            {"artifact_id": "missing_thumb", "artifact_type": "thumbnail", "path": str(workspace / "nope.png")},
        ],
    }
    resp = run(doc, workspace)
    by_id = {a["artifact_id"]: a for a in resp["provenance"]["artifacts"]}
    assert by_id["main_video"]["present"] is True
    assert by_id["main_video"]["fingerprint"] is not None
    assert by_id["missing_thumb"]["present"] is False
    assert by_id["missing_thumb"]["fingerprint"] is None


def test_cache_reuse_round_trips_a_delivery_package_report(media, workspace):
    from qc_skill.cache import QCReportCache

    cache = QCReportCache(directory=workspace / ".qc-cache")
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": str(media["clean"])}],
        "rules": {"delivery_package": {"artifacts": [{"artifact_id": "main_video", "required": True}]}},
    }
    first = run(doc, workspace, cache=cache)
    assert first["reused"] is False
    second = run(doc, workspace, cache=cache)
    assert second["reused"] is True
    assert second["report"]["overall_status"] == first["report"]["overall_status"]
