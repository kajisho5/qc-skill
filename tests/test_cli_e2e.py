import json
import subprocess
import sys

CLI = [sys.executable, "-m", "qc_skill.cli"]


def _run_cli(*args, input_text=None, cwd=None):
    return subprocess.run(CLI + list(args), input=input_text, capture_output=True, text=True, cwd=cwd)


def test_cli_contract_json():
    result = _run_cli("contract", "--json")
    assert result.returncode == 0
    doc = json.loads(result.stdout)
    assert doc["skill_id"] == "qc"


def test_cli_doctor_json():
    result = _run_cli("doctor", "--json")
    doc = json.loads(result.stdout)
    assert doc["status"] in ("ok", "degraded")
    assert result.returncode in (0, 2)


def test_cli_run_from_stdin(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    result = _run_cli("run", "-", "--json", "--workspace", str(workspace), "--no-cache", input_text=json.dumps(doc), cwd=str(workspace))
    assert result.returncode == 0, result.stderr
    resp = json.loads(result.stdout)
    assert resp["status"] == "completed"
    assert resp["report"]["kind"] == "video"


def test_cli_run_from_file(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    request_file = workspace / "req.json"
    request_file.write_text(json.dumps(doc))
    result = _run_cli("run", str(request_file), "--json", "--workspace", str(workspace), "--no-cache")
    assert result.returncode == 0, result.stderr
    resp = json.loads(result.stdout)
    assert resp["status"] == "completed"


def test_cli_run_malformed_json_fails_cleanly(workspace):
    result = _run_cli("run", "-", "--json", "--workspace", str(workspace), input_text="{not valid json")
    assert result.returncode == 2  # INVALID_REQUEST
    resp = json.loads(result.stdout)
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "INVALID_REQUEST"


def test_cli_run_qc_fail_is_still_a_completed_execution(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"expected_width": 99999}},
    }
    result = _run_cli("run", "-", "--json", "--workspace", str(workspace), "--no-cache", input_text=json.dumps(doc))
    assert result.returncode == 0  # execution succeeded
    resp = json.loads(result.stdout)
    assert resp["status"] == "completed"
    assert resp["report"]["overall_status"] == "FAIL"


def test_cli_run_missing_input_file_nonzero_exit(workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(workspace / "nope.mp4")}
    result = _run_cli("run", "-", "--json", "--workspace", str(workspace), input_text=json.dumps(doc))
    resp = json.loads(result.stdout)
    assert resp["error"]["code"] == "MISSING_INPUT"
    assert result.returncode != 0
