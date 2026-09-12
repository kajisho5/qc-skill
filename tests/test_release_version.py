"""scripts/release_version.py - the release workflow's version read/bump
helper. Tested directly (not just smoke-tested inside CI) because a bug
here would either publish a wrong version number or silently corrupt
pyproject.toml.
"""

import pytest

import subprocess
import sys
from pathlib import Path

from scripts.release_version import bump_semver, parse_semver, read_version, write_version


def _write_pyproject(path, version="1.2.3"):
    p = path / "pyproject.toml"
    p.write_text(
        f'[project]\nname = "example"\nversion = "{version}"\ndescription = "x"\n',
        encoding="utf-8",
    )
    return p


def test_read_version(tmp_path):
    p = _write_pyproject(tmp_path, "0.3.1")
    assert read_version(p) == "0.3.1"


def test_read_version_missing_line_raises(tmp_path):
    p = tmp_path / "pyproject.toml"
    p.write_text('[project]\nname = "example"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        read_version(p)


def test_write_version_replaces_only_the_version_line(tmp_path):
    p = _write_pyproject(tmp_path, "0.3.1")
    original = p.read_text(encoding="utf-8")
    write_version(p, "0.4.0")
    updated = p.read_text(encoding="utf-8")
    assert 'version = "0.4.0"' in updated
    assert 'version = "0.3.1"' not in updated
    # everything else in the file is untouched
    assert updated.replace("0.4.0", "0.3.1") == original


def test_parse_semver():
    assert parse_semver("1.2.3") == (1, 2, 3)


@pytest.mark.parametrize("bad", ["1.2", "1.2.3.4", "v1.2.3", "1.2.x", ""])
def test_parse_semver_rejects_non_semver(bad):
    with pytest.raises(ValueError):
        parse_semver(bad)


@pytest.mark.parametrize(
    "version,level,expected",
    [
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("0.1.0", "patch", "0.1.1"),
        ("0.9.9", "minor", "0.10.0"),
    ],
)
def test_bump_semver(version, level, expected):
    assert bump_semver(version, level) == expected


def test_bump_semver_rejects_unknown_level():
    with pytest.raises(ValueError):
        bump_semver("1.0.0", "epoch")


def test_read_then_bump_then_write_round_trip(tmp_path):
    p = _write_pyproject(tmp_path, "2.5.9")
    current = read_version(p)
    new_version = bump_semver(current, "minor")
    write_version(p, new_version)
    assert read_version(p) == "2.6.0"


def _run_cli(*args, cwd):
    script = Path(__file__).resolve().parents[1] / "scripts" / "release_version.py"
    return subprocess.run(
        [sys.executable, str(script), *args], cwd=cwd, capture_output=True, text=True, check=True,
    )


def test_cli_get(tmp_path):
    _write_pyproject(tmp_path, "1.0.0")
    result = _run_cli("get", cwd=tmp_path)
    assert result.stdout.strip() == "1.0.0"


def test_cli_bump_writes_and_prints_new_version(tmp_path):
    p = _write_pyproject(tmp_path, "1.0.0")
    result = _run_cli("bump", "patch", cwd=tmp_path)
    assert result.stdout.strip() == "1.0.1"
    assert read_version(p) == "1.0.1"


def test_cli_set_writes_an_exact_version_from_release_drafter(tmp_path):
    p = _write_pyproject(tmp_path, "1.0.0")
    result = _run_cli("set", "3.4.5", cwd=tmp_path)
    assert result.stdout.strip() == "3.4.5"
    assert read_version(p) == "3.4.5"


def test_cli_set_rejects_a_malformed_version(tmp_path):
    _write_pyproject(tmp_path, "1.0.0")
    script = Path(__file__).resolve().parents[1] / "scripts" / "release_version.py"
    result = subprocess.run(
        [sys.executable, str(script), "set", "not-a-version"], cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode != 0
