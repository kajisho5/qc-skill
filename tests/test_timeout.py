"""request.timeout is enforced (ADR-019): the whole request runs under one
wall-clock budget, a child still running when it elapses is killed with
its whole process group, and the call fails with the TIMEOUT error."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

from qc_skill import runner
from qc_skill.errors import ERROR_CODES, QCError
from qc_skill.runner import check_deadline, request_deadline, run_argv
from tests.helpers import run

CLI = [sys.executable, "-m", "qc_skill.cli"]


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) != 0  # WAIT_OBJECT_0: it has exited
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a reparented zombie nobody has reaped yet is not running anything
        with open(f"/proc/{pid}/stat", "r") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def _wait_gone(pid: int, seconds: float = 10.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _group_gone(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture()
def spawned(monkeypatch):
    """Record every child run_argv starts."""

    procs = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", recording_popen)
    return procs


def _assert_no_children(procs):
    for proc in procs:
        assert proc.poll() is not None, f"child {proc.pid} still running"
        if sys.platform != "win32":
            assert _group_gone(proc.pid), f"process group {proc.pid} still has members"


def test_timeout_code_is_retryable_with_its_own_exit_code():
    assert ERROR_CODES["TIMEOUT"] == (15, True)
    assert len({exit_code for exit_code, _ in ERROR_CODES.values()}) == len(ERROR_CODES)


@pytest.mark.parametrize("kind", ["video", "audio", "delivery"])
def test_tiny_timeout_returns_timeout_and_leaves_no_child(media, workspace, spawned, kind):
    doc = {"operation": "inspect", "kind": kind, "input": str(media["clean"]), "timeout": 0.01}
    with pytest.raises(QCError) as excinfo:
        run(doc, workspace)
    err = excinfo.value
    assert err.code == "TIMEOUT"
    assert err.exit_code == 15
    assert err.retryable is True
    assert err.details["timeout"] == 0.01
    _assert_no_children(spawned)


def test_running_ffmpeg_is_killed_when_the_budget_elapses(media, spawned):
    # -re paces a 10-minute synthetic source in real time, so ffmpeg is
    # certainly still running when the half-second budget elapses.
    ffmpeg = shutil.which("ffmpeg")
    argv = [ffmpeg, "-hide_banner", "-nostdin", "-re", "-f", "lavfi", "-i", "testsrc=duration=600", "-f", "null", "-"]
    start = time.monotonic()
    with request_deadline(0.5):
        with pytest.raises(QCError) as excinfo:
            run_argv(argv, timeout=120)
    assert excinfo.value.code == "TIMEOUT"
    assert excinfo.value.details["stage"].startswith("ffmpeg")
    assert time.monotonic() - start < 30
    assert len(spawned) == 1
    _assert_no_children(spawned)


def test_whole_process_tree_is_killed(tmp_path):
    """The child starts a grandchild of its own; both must be gone."""

    pid_file = tmp_path / "grandchild.pid"
    script = textwrap.dedent(
        """
        import subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        with open(sys.argv[1], "w") as fh:
            fh.write(str(child.pid))
        time.sleep(120)
        """
    )
    start = time.monotonic()
    with request_deadline(5.0):
        with pytest.raises(QCError) as excinfo:
            run_argv([sys.executable, "-c", script, str(pid_file)], timeout=120)
    elapsed = time.monotonic() - start
    assert excinfo.value.code == "TIMEOUT"
    assert elapsed < 60
    assert pid_file.exists(), "the child never started its grandchild within the budget"
    grandchild = int(pid_file.read_text())
    assert _wait_gone(grandchild), f"grandchild {grandchild} survived the timeout"


def test_per_call_timeout_without_a_request_budget_is_not_timeout():
    result = run_argv([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert result.timed_out is True


def test_spent_budget_refuses_to_start_a_child(spawned):
    with request_deadline(0.01):
        time.sleep(0.05)
        with pytest.raises(QCError) as excinfo:
            run_argv([sys.executable, "-c", "pass"], timeout=10)
        with pytest.raises(QCError):
            check_deadline("between children")
    assert excinfo.value.code == "TIMEOUT"
    assert spawned == []
    check_deadline("outside any request")  # no budget, never raises


def test_nested_budget_never_extends_the_outer_one():
    with request_deadline(0.01):
        time.sleep(0.05)
        with request_deadline(300):
            with pytest.raises(QCError):
                check_deadline("inner")


def test_generous_timeout_completes_normally(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"]), "timeout": 300}
    response = run(doc, workspace)
    assert response["status"] == "completed"


def test_cli_tiny_timeout_exit_code_and_error_shape(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"]), "timeout": 0.01}
    result = subprocess.run(
        CLI + ["run", "-", "--json", "--workspace", str(workspace), "--no-cache"],
        input=json.dumps(doc), capture_output=True, text=True, cwd=str(workspace), timeout=120,
    )
    assert result.returncode == 15, result.stderr
    resp = json.loads(result.stdout)
    assert resp["schema"] == "qc/response@1"
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "TIMEOUT"
    assert resp["error"]["retryable"] is True
    assert resp["error"]["details"]["timeout"] == 0.01
