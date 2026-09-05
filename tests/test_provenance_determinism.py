import datetime as dt

from tests.helpers import run


def test_identity_is_stable_across_runs_with_different_clocks(media, workspace):
    doc = {"operation": "check", "kind": "video", "input": str(media["clean"]), "rules": {"video": {"expected_width": 320}}}

    workspace_a = workspace / "a"
    workspace_b = workspace / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    first = run(doc, workspace_a)
    second = run(doc, workspace_b)

    assert first["provenance"]["identity"] == second["provenance"]["identity"]
    assert first["report"]["id"] == second["report"]["id"]


def test_identity_changes_when_rules_change(media, workspace):
    doc1 = {"operation": "check", "kind": "video", "input": str(media["clean"]), "rules": {"video": {"expected_width": 320}}}
    doc2 = {"operation": "check", "kind": "video", "input": str(media["clean"]), "rules": {"video": {"expected_width": 321}}}
    r1 = run(doc1, workspace)
    r2 = run(doc2, workspace)
    assert r1["provenance"]["identity"] != r2["provenance"]["identity"]


def test_identity_changes_when_content_changes(media, workspace):
    doc_clean = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    doc_black = {"operation": "inspect", "kind": "video", "input": str(media["black"])}
    r1 = run(doc_clean, workspace)
    r2 = run(doc_black, workspace)
    assert r1["provenance"]["identity"] != r2["provenance"]["identity"]


def test_identity_excludes_asset_id_style_labels_by_construction(media, workspace):
    # There is no request_id/asset_id field accepted into the identity hash
    # at all - passing one must not change the computed identity.
    doc1 = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    doc2 = {"operation": "inspect", "kind": "video", "input": str(media["clean"]), "request_id": "totally-different-label"}
    r1 = run(doc1, workspace)
    r2 = run(doc2, workspace)
    assert r1["provenance"]["identity"] == r2["provenance"]["identity"]


def test_provenance_records_observed_source_not_ai(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    assert resp["provenance"]["measurement_source"] == "OBSERVED"
    for m in resp["report"]["measurements"]:
        assert m["source"] != "AI_GENERATED"


def test_observed_at_is_utc_iso8601_with_z_suffix(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    resp = run(doc, workspace)
    observed_at = resp["provenance"]["observed_at"]
    assert observed_at.endswith("Z")
    dt.datetime.fromisoformat(observed_at[:-1])  # must parse as a valid timestamp
