import pytest

from qc_skill.errors import QCError
from qc_skill.schemas import parse_request


def base_doc(**overrides):
    doc = {"operation": "inspect", "kind": "video", "input": "a.mp4"}
    doc.update(overrides)
    return doc


def test_parse_request_minimal_ok():
    req = parse_request(base_doc())
    assert req.operation == "inspect"
    assert req.kind == "video"
    assert req.cache_policy == "use"


@pytest.mark.parametrize("key", ["command", "commands", "argv", "args", "shell", "cmd", "exec", "executable", "filter", "filter_complex", "env", "environment"])
def test_parse_request_rejects_forbidden_keys_top_level(key):
    doc = base_doc(**{key: "anything"})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_forbidden_keys_nested():
    doc = base_doc(parameters={"nested": {"shell": True}})
    with pytest.raises(QCError):
        parse_request(doc)


def test_parse_request_rejects_unknown_top_level_key():
    doc = base_doc(bogus="x")
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_non_object():
    with pytest.raises(QCError) as exc:
        parse_request(["not", "an", "object"])
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_bad_operation():
    doc = base_doc(operation="delete_everything")
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "UNSUPPORTED_OPERATION"


def test_parse_request_rejects_bad_kind():
    doc = base_doc(kind="thumbnail")
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_missing_input():
    doc = base_doc(input="")
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "MISSING_INPUT"


def test_parse_request_rejects_missing_input_key():
    doc = {"operation": "inspect", "kind": "video"}
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "MISSING_INPUT"


def test_parse_request_rejects_unknown_parameter_key():
    doc = base_doc(parameters={"totally_made_up": 1})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_unknown_rule_section():
    doc = base_doc(rules={"bogus_kind": {}})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_unknown_rule_field():
    doc = base_doc(rules={"video": {"expected_width": 100, "not_a_real_field": True}})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_accepts_typed_video_rule():
    doc = base_doc(operation="check", rules={"video": {"expected_width": 1920, "expected_height": 1080}})
    req = parse_request(doc)
    assert req.video_rule.expected_width == 1920
    assert req.video_rule.expected_height == 1080


def test_parse_request_rejects_invalid_cache_policy():
    doc = base_doc(cache_policy="sometimes")
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_negative_timeout():
    doc = base_doc(timeout=-5)
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_TIME_RANGE"


def test_parse_request_rejects_bool_timeout():
    doc = base_doc(timeout=True)
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_TIME_RANGE"


def test_parse_request_nested_delivery_rule():
    doc = base_doc(
        kind="delivery",
        operation="validate",
        rules={"delivery": {"expected_extension": "mp4", "video": {"expected_width": 1920}}},
    )
    req = parse_request(doc)
    assert req.delivery_rule.expected_extension == "mp4"
    assert req.delivery_rule.video.expected_width == 1920


def _package_doc(**overrides):
    doc = {
        "operation": "inspect", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "main_video", "artifact_type": "video", "path": "a.mp4"}],
    }
    doc.update(overrides)
    return doc


def test_parse_request_delivery_package_minimal_ok():
    req = parse_request(_package_doc())
    assert req.input is None
    assert req.artifacts[0].artifact_id == "main_video"
    assert req.artifacts[0].artifact_type == "video"
    assert req.artifacts[0].path == "a.mp4"


def test_parse_request_delivery_package_requires_non_empty_artifacts():
    with pytest.raises(QCError) as exc:
        parse_request(_package_doc(artifacts=[]))
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_artifacts_rejected_for_non_package_kind():
    doc = base_doc(artifacts=[{"artifact_id": "x", "artifact_type": "video", "path": "a.mp4"}])
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_duplicate_artifact_ids():
    doc = _package_doc(artifacts=[
        {"artifact_id": "dup", "artifact_type": "video", "path": "a.mp4"},
        {"artifact_id": "dup", "artifact_type": "subtitle", "path": "a.srt"},
    ])
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_unknown_artifact_type():
    doc = _package_doc(artifacts=[{"artifact_id": "x", "artifact_type": "banana", "path": "a.mp4"}])
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_unknown_artifact_entry_field():
    doc = _package_doc(artifacts=[{"artifact_id": "x", "artifact_type": "video", "path": "a.mp4", "fingerprint": "deadbeef"}])
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_delivery_package_rule_with_nested_video_rule():
    doc = _package_doc(
        operation="check",
        rules={"delivery_package": {"artifacts": [
            {"artifact_id": "main_video", "required": True, "video": {"expected_width": 1920}},
        ]}},
    )
    req = parse_request(doc)
    artifact_rule = req.delivery_package_rule.artifacts[0]
    assert artifact_rule.artifact_id == "main_video"
    assert artifact_rule.required is True
    assert artifact_rule.video.expected_width == 1920


def test_parse_request_rejects_duplicate_artifact_ids_in_rule():
    doc = _package_doc(
        rules={"delivery_package": {"artifacts": [
            {"artifact_id": "dup"}, {"artifact_id": "dup"},
        ]}},
    )
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_delivery_package_artifact_rule_without_id():
    doc = _package_doc(rules={"delivery_package": {"artifacts": [{"required": True}]}})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_cross_artifact_duration_consistency():
    doc = _package_doc(rules={"delivery_package": {"cross_artifact": {"duration_consistency": [
        {"artifact_ids": ["a", "b"], "max_delta_sec": 0.5},
    ]}}})
    req = parse_request(doc)
    rule = req.delivery_package_rule.cross_artifact.duration_consistency[0]
    assert rule.artifact_ids == ["a", "b"]
    assert rule.max_delta_sec == 0.5


def test_parse_request_cross_artifact_dependency():
    doc = _package_doc(rules={"delivery_package": {"cross_artifact": {"dependencies": [
        {"artifact_id": "a", "requires_artifact_id": "b"},
    ]}}})
    req = parse_request(doc)
    rule = req.delivery_package_rule.cross_artifact.dependencies[0]
    assert rule.artifact_id == "a"
    assert rule.requires_artifact_id == "b"


def test_parse_request_rejects_duration_consistency_with_one_artifact_id():
    doc = _package_doc(rules={"delivery_package": {"cross_artifact": {"duration_consistency": [
        {"artifact_ids": ["a"], "max_delta_sec": 0.5},
    ]}}})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_negative_max_delta_sec():
    doc = _package_doc(rules={"delivery_package": {"cross_artifact": {"duration_consistency": [
        {"artifact_ids": ["a", "b"], "max_delta_sec": -1},
    ]}}})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_unknown_cross_artifact_field():
    doc = _package_doc(rules={"delivery_package": {"cross_artifact": {"bogus": []}}})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"


def test_parse_request_rejects_dependency_missing_field():
    doc = _package_doc(rules={"delivery_package": {"cross_artifact": {"dependencies": [
        {"artifact_id": "a"},
    ]}}})
    with pytest.raises(QCError) as exc:
        parse_request(doc)
    assert exc.value.code == "INVALID_REQUEST"
