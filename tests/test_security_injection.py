"""Security boundary tests (STEP 15/23 of the task spec).

These exercise the request document as the only attacker-controlled
surface: forbidden keys, path traversal, symlink escape, and an attempt to
smuggle shell metacharacters through a plain filename argument (which must
be treated as a literal, non-existent path - never interpreted by a shell).
"""

import os
import sys

import pytest

from qc_skill.errors import QCError
from tests.helpers import run


@pytest.mark.parametrize(
    "payload",
    [
        {"command": ["rm", "-rf", "/"]},
        {"argv": ["ffmpeg", "-i", "x"]},
        {"shell": True},
        {"executable": "/bin/sh"},
        {"filter": "concat=..."},
        {"filter_complex": "movie=/etc/passwd"},
        {"env": {"LD_PRELOAD": "/tmp/evil.so"}},
    ],
)
def test_forbidden_keys_are_rejected(media, workspace, payload):
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    doc.update(payload)
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "INVALID_REQUEST"


def test_shell_metacharacters_in_input_are_treated_as_a_literal_filename(workspace):
    doc = {"operation": "inspect", "kind": "video", "input": "; touch /tmp/qc-pwned ; echo"}
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "MISSING_INPUT"
    assert not os.path.exists("/tmp/qc-pwned")


def test_path_traversal_in_input_is_rejected(media, workspace):
    doc = {"operation": "inspect", "kind": "video", "input": "../../../../etc/passwd"}
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "PATH_NOT_ALLOWED"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_symlink_escape_outside_allowed_root_is_rejected(media, workspace):
    allowed = workspace / "allowed"
    allowed.mkdir()
    link = allowed / "escape.mp4"
    os.symlink(media["clean"], link)

    doc = {"operation": "inspect", "kind": "video", "input": str(link)}
    with pytest.raises(QCError) as exc:
        run(doc, workspace, allowed_roots=[str(allowed)])
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_input_outside_allowed_root_is_rejected_even_if_it_exists(media, workspace):
    allowed = workspace / "allowed"
    allowed.mkdir()
    doc = {"operation": "inspect", "kind": "video", "input": str(media["clean"])}
    with pytest.raises(QCError) as exc:
        run(doc, workspace, allowed_roots=[str(allowed)])
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_rule_payload_cannot_carry_forbidden_keys_either(media, workspace):
    doc = {
        "operation": "check", "kind": "video", "input": str(media["clean"]),
        "rules": {"video": {"expected_width": 1, "shell": True}},
    }
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "INVALID_REQUEST"


def test_unknown_operation_is_rejected_not_silently_ignored(media, workspace):
    doc = {"operation": "auto_fix", "kind": "video", "input": str(media["clean"])}
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "UNSUPPORTED_OPERATION"
