import json

from qc_skill.cache import QCReportCache
from qc_skill.errors import QCError
from tests.helpers import run

import pytest


def test_second_run_is_reused(media, workspace):
    cache = QCReportCache(workspace / ".qc-cache")
    doc = {"operation": "check", "kind": "video", "input": str(media["clean"])}

    first = run(doc, workspace, cache=cache)
    assert first["reused"] is False
    assert first["cache"]["status"] == "miss"

    second = run(doc, workspace, cache=cache)
    assert second["reused"] is True
    assert second["cache"]["status"] == "hit"
    assert second["report"] == first["report"]


def test_different_rules_are_not_reused(media, workspace):
    cache = QCReportCache(workspace / ".qc-cache")
    doc1 = {"operation": "check", "kind": "video", "input": str(media["clean"]), "rules": {"video": {"expected_width": 320}}}
    doc2 = {"operation": "check", "kind": "video", "input": str(media["clean"]), "rules": {"video": {"expected_width": 999}}}

    run(doc1, workspace, cache=cache)
    second = run(doc2, workspace, cache=cache)
    assert second["reused"] is False


def test_tampered_cache_file_is_treated_as_invalid_not_a_crash(media, workspace):
    cache = QCReportCache(workspace / ".qc-cache")
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    first = run(doc, workspace, cache=cache)
    key = first["cache"]["key"]

    cache_file = workspace / ".qc-cache" / key[:2] / f"{key}.json"
    assert cache_file.exists()
    entry = json.loads(cache_file.read_text())
    entry["report"]["overall_status"] = "PASS"  # tamper without updating result_hash
    cache_file.write_text(json.dumps(entry))

    second = run(doc, workspace, cache=cache)
    assert second["reused"] is False
    assert second["cache"]["status"] == "miss"


def test_cache_policy_only_without_entry_raises_validation_error(media, workspace):
    cache = QCReportCache(workspace / ".qc-cache")
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"]), "cache_policy": "only"}
    with pytest.raises(QCError) as exc:
        run(doc, workspace, cache=cache)
    assert exc.value.code == "VALIDATION_ERROR"


def test_cache_policy_bypass_never_reuses(media, workspace):
    cache = QCReportCache(workspace / ".qc-cache")
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"]), "cache_policy": "bypass"}
    run(doc, workspace, cache=cache)
    second = run(doc, workspace, cache=cache)
    assert second["reused"] is False
