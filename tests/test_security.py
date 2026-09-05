import os
import sys

import pytest

from qc_skill.errors import QCError
from qc_skill.security import PathPolicy, check_filename


def test_resolve_input_accepts_regular_file(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"data")
    policy = PathPolicy(workspace=str(tmp_path))
    resolved = policy.resolve_input(str(f))
    assert resolved == f.resolve()


def test_resolve_input_rejects_missing_file(tmp_path):
    policy = PathPolicy(workspace=str(tmp_path))
    with pytest.raises(QCError) as exc:
        policy.resolve_input(str(tmp_path / "missing.mp4"))
    assert exc.value.code == "MISSING_INPUT"


def test_resolve_input_rejects_traversal(tmp_path):
    policy = PathPolicy(workspace=str(tmp_path))
    with pytest.raises(QCError) as exc:
        policy.resolve_input("../../etc/passwd")
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_resolve_input_rejects_traversal_embedded_windows_style(tmp_path):
    policy = PathPolicy(workspace=str(tmp_path))
    with pytest.raises(QCError) as exc:
        policy.resolve_input("safe\\..\\..\\secret.mp4")
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_resolve_input_rejects_nul_byte(tmp_path):
    policy = PathPolicy(workspace=str(tmp_path))
    with pytest.raises(QCError) as exc:
        policy.resolve_input("a.mp4\x00.txt")
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_resolve_input_rejects_directory(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    policy = PathPolicy(workspace=str(tmp_path))
    with pytest.raises(QCError) as exc:
        policy.resolve_input(str(d))
    assert exc.value.code == "INVALID_INPUT"


def test_resolve_input_enforces_allowed_roots(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    f = outside / "a.mp4"
    f.write_bytes(b"data")

    policy = PathPolicy(workspace=str(tmp_path), allowed_input_roots=[str(allowed)])
    with pytest.raises(QCError) as exc:
        policy.resolve_input(str(f))
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_resolve_input_prefix_collision_is_not_allowed(tmp_path):
    allowed = tmp_path / "media"
    allowed.mkdir()
    evil_dir = tmp_path / "media_evil"
    evil_dir.mkdir()
    f = evil_dir / "a.mp4"
    f.write_bytes(b"data")

    policy = PathPolicy(workspace=str(tmp_path), allowed_input_roots=[str(allowed)])
    with pytest.raises(QCError) as exc:
        policy.resolve_input(str(f))
    assert exc.value.code == "PATH_NOT_ALLOWED"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_resolve_input_rejects_symlink_escape(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.mp4"
    secret.write_bytes(b"data")
    link = allowed / "link.mp4"
    os.symlink(secret, link)

    policy = PathPolicy(workspace=str(tmp_path), allowed_input_roots=[str(allowed)])
    with pytest.raises(QCError) as exc:
        policy.resolve_input(str(link))
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_resolve_output_stays_within_workspace(tmp_path):
    policy = PathPolicy(workspace=str(tmp_path))
    resolved = policy.resolve_output("reports/out.json")
    assert resolved == (tmp_path / "reports" / "out.json").resolve()


def test_resolve_output_rejects_absolute_path(tmp_path):
    policy = PathPolicy(workspace=str(tmp_path))
    with pytest.raises(QCError) as exc:
        policy.resolve_output("/etc/passwd")
    assert exc.value.code == "OUTPUT_ERROR"


def test_resolve_output_rejects_traversal(tmp_path):
    policy = PathPolicy(workspace=str(tmp_path))
    with pytest.raises(QCError) as exc:
        policy.resolve_output("../escape.json")
    assert exc.value.code == "OUTPUT_ERROR"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX symlinks")
def test_resolve_output_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "link_dir").symlink_to(outside)

    policy = PathPolicy(workspace=str(workspace))
    with pytest.raises(QCError) as exc:
        policy.resolve_output("link_dir/out.json")
    assert exc.value.code == "OUTPUT_ERROR"


@pytest.mark.parametrize("name", ["CON", "con.txt", "PRN.mp4", "COM1", "LPT9.json"])
def test_check_filename_rejects_windows_reserved_names(name):
    with pytest.raises(QCError) as exc:
        check_filename(name)
    assert exc.value.code == "OUTPUT_ERROR"


@pytest.mark.parametrize("name", ["-rf", "trailing.", "trailing ", "bad<name>.json", "a\x01b"])
def test_check_filename_rejects_unsafe_names(name):
    with pytest.raises(QCError):
        check_filename(name)


def test_check_filename_accepts_normal_name():
    check_filename("report-2026-01-01.json")
