"""Timeline integrity (Phase 3, ADR-012): SubtitleRule.timeline_integrity
checks a subtitle's delivery-timeline cue timing against a caller-supplied
TimelineMap, never constructing or inferring the timeline itself.

media["subtitle_valid"] has two cues: 0.2-1.8 and 2.0-3.8 (in the
*delivery* timeline, since it's the actual file). Each test supplies a
different source-timeline + TimelineSegment mapping that should produce
those exact delivery times when correct.
"""

import pytest

from qc_skill.errors import QCError

from tests.helpers import checks_by_id, run


def test_identity_mapping_passes(media, workspace):
    # No edit at all: source time == delivery time.
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            "timeline": [{"source_start": 0, "source_end": 100, "delivery_start": 0, "speed": 1.0}],
            "source_cues": [{"start": 0.2, "end": 1.8}, {"start": 2.0, "end": 3.8}],
            "tolerance_sec": 0.05,
        }}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.timeline_mapping_matches_source"]["status"] == "PASS"
    assert resp["report"]["overall_status"] == "PASS"


def test_trim_mapping_passes(media, workspace):
    # Source had 5s trimmed from the front; source cues at 5.2-6.8/7.0-8.8
    # should land at delivery 0.2-1.8/2.0-3.8 after the trim.
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            "timeline": [{"source_start": 5.0, "source_end": 105.0, "delivery_start": 0.0, "speed": 1.0}],
            "source_cues": [{"start": 5.2, "end": 6.8}, {"start": 7.0, "end": 8.8}],
            "tolerance_sec": 0.05,
        }}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.timeline_mapping_matches_source"]["status"] == "PASS"


def test_speed_change_mapping_passes(media, workspace):
    # Source played back at 2x speed: source cues at double the delivery time.
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            "timeline": [{"source_start": 0.0, "source_end": 200.0, "delivery_start": 0.0, "speed": 2.0}],
            "source_cues": [{"start": 0.4, "end": 3.6}, {"start": 4.0, "end": 7.6}],
            "tolerance_sec": 0.05,
        }}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.timeline_mapping_matches_source"]["status"] == "PASS"


def test_mapping_mismatch_fails(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            "timeline": [{"source_start": 0, "source_end": 100, "delivery_start": 0, "speed": 1.0}],
            # Wrong expected timing (off by 2s, well past tolerance).
            "source_cues": [{"start": 2.2, "end": 3.8}, {"start": 4.0, "end": 5.8}],
            "tolerance_sec": 0.05,
        }}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.timeline_mapping_matches_source"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "SUBTITLE_TIMELINE_MAPPING_MISMATCH" in codes
    assert resp["report"]["overall_status"] == "FAIL"


def test_cue_count_mismatch_fails(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            "timeline": [{"source_start": 0, "source_end": 100, "delivery_start": 0, "speed": 1.0}],
            "source_cues": [{"start": 0.2, "end": 1.8}],  # only 1, actual has 2
            "tolerance_sec": 0.05,
        }}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.timeline_mapping_matches_source"]["status"] == "FAIL"
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "SUBTITLE_TIMELINE_CUE_COUNT_MISMATCH" in codes


def test_source_cue_in_cut_region_is_unknown(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            # Only covers [0, 1.9) -> the second source cue (2.0-3.8) is
            # entirely outside every segment (a cut region).
            "timeline": [{"source_start": 0, "source_end": 1.9, "delivery_start": 0, "speed": 1.0}],
            "source_cues": [{"start": 0.2, "end": 1.8}, {"start": 2.0, "end": 3.8}],
            "tolerance_sec": 0.05,
        }}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    check = checks["subtitle.timeline_mapping_matches_source"]
    assert check["status"] == "UNKNOWN"
    assert check["reason"]
    codes = {f["code"] for f in resp["report"]["findings"]}
    assert "SUBTITLE_TIMELINE_MAPPING_MISMATCH" not in codes
    assert "SUBTITLE_TIMELINE_CUE_COUNT_MISMATCH" not in codes


def test_no_timeline_integrity_rule_means_no_check(media, workspace):
    doc = {"operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]), "rules": {"subtitle": {}}}
    resp = run(doc, workspace)
    check_ids = {c["check_id"] for c in resp["report"]["checks"]}
    assert "subtitle.timeline_mapping_matches_source" not in check_ids


def test_reused_via_delivery_package_artifact_subtitle_sub_rule(media, workspace):
    # Same rule, no new plumbing: nested inside a delivery_package artifact's
    # own subtitle sub-rule (demonstrates the ADR-012 reuse claim).
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "captions", "artifact_type": "subtitle", "path": str(media["subtitle_valid"])}],
        "rules": {"delivery_package": {"artifacts": [{
            "artifact_id": "captions",
            "subtitle": {"timeline_integrity": {
                "timeline": [{"source_start": 0, "source_end": 100, "delivery_start": 0, "speed": 1.0}],
                "source_cues": [{"start": 0.2, "end": 1.8}, {"start": 2.0, "end": 3.8}],
                "tolerance_sec": 0.05,
            }},
        }]}},
    }
    resp = run(doc, workspace)
    matches = [c for c in resp["report"]["checks"] if c["check_id"] == "subtitle.timeline_mapping_matches_source"]
    assert len(matches) == 1
    assert matches[0]["status"] == "PASS"
    assert matches[0]["artifact_id"] == "captions"


def test_schema_rejects_zero_speed(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            "timeline": [{"source_start": 0, "source_end": 100, "delivery_start": 0, "speed": 0}],
            "source_cues": [],
        }}},
    }
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "INVALID_REQUEST"


def test_schema_rejects_segment_missing_required_field(media, workspace):
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(media["subtitle_valid"]),
        "rules": {"subtitle": {"timeline_integrity": {
            "timeline": [{"source_start": 0, "delivery_start": 0}],
            "source_cues": [],
        }}},
    }
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "INVALID_REQUEST"
