"""scripts/release_decide.py - decides the next release version and
whether pyproject.toml needs a version bump. Pure-logic tests for
decide_release(); subprocess tests for the CLI wrapper (including real
git tag queries) against a throwaway git repo.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.release_decide import decide_release, tag_exists


def test_no_tag_yet_uses_current_version_as_is():
    decision = decide_release(current_version="0.1.0", latest_tag_version=None, resolved_version=None)
    assert decision.new_version == "0.1.0"
    assert decision.needs_pyproject_bump is False


def test_manual_bump_already_happened_is_respected():
    # pyproject says 0.3.0 but the last tag was 0.2.0 -> someone bumped by hand.
    decision = decide_release(current_version="0.3.0", latest_tag_version="0.2.0", resolved_version=None)
    assert decision.new_version == "0.3.0"
    assert decision.needs_pyproject_bump is False


def test_matching_tag_triggers_auto_bump_from_resolved_version():
    decision = decide_release(current_version="0.2.0", latest_tag_version="0.2.0", resolved_version="0.2.1")
    assert decision.new_version == "0.2.1"
    assert decision.needs_pyproject_bump is True


def test_matching_tag_without_resolved_version_is_an_error():
    with pytest.raises(ValueError):
        decide_release(current_version="0.2.0", latest_tag_version="0.2.0", resolved_version=None)


def _init_repo(tmp_path, version="0.1.0"):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "__init__.py").write_text("", encoding="utf-8")
    repo_scripts = Path(__file__).resolve().parents[1] / "scripts"
    (scripts_dir / "release_version.py").write_text((repo_scripts / "release_version.py").read_text(encoding="utf-8"), encoding="utf-8")
    (scripts_dir / "release_decide.py").write_text((repo_scripts / "release_decide.py").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "example"\nversion = "{version}"\n', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Initial commit"], cwd=tmp_path, check=True)


def _run_decide_cli(tmp_path, *extra_args):
    return subprocess.run(
        [sys.executable, "-m", "scripts.release_decide", *extra_args],
        cwd=tmp_path, capture_output=True, text=True,
    )


def test_cli_bootstrap_no_tags(tmp_path):
    _init_repo(tmp_path, version="0.1.0")
    result = _run_decide_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    out = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert out["new_version"] == "0.1.0"
    assert out["tag"] == "v0.1.0"
    assert out["needs_pyproject_bump"] == "false"
    assert out["needs_release"] == "true"


def test_cli_tag_already_exists_is_a_no_op(tmp_path):
    # pyproject and the latest tag both already say 0.1.0 - nothing to do,
    # even if (as the real workflow always would in this branch) release-drafter
    # is still asked and happens to resolve to that same already-tagged version.
    _init_repo(tmp_path, version="0.1.0")
    subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, check=True)
    result = _run_decide_cli(tmp_path, "--resolved-version", "0.1.0")
    assert result.returncode == 0, result.stderr
    out = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert out["needs_release"] == "false"


def test_cli_manual_bump_ahead_of_latest_tag_is_released_as_is(tmp_path):
    # pyproject was hand-bumped to 0.3.0 but only v0.2.0 has been tagged so
    # far - release 0.3.0 directly, no release-drafter call needed, and
    # never let the auto-bump path silently overwrite the hand-chosen
    # number.
    _init_repo(tmp_path, version="0.3.0")
    subprocess.run(["git", "tag", "v0.2.0"], cwd=tmp_path, check=True)
    result = _run_decide_cli(tmp_path)
    assert result.returncode == 0, result.stderr
    out = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert out["new_version"] == "0.3.0"
    assert out["needs_pyproject_bump"] == "false"
    assert out["needs_release"] == "true"


def test_cli_auto_bump_path_requires_resolved_version(tmp_path):
    _init_repo(tmp_path, version="0.1.0")
    subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, check=True)
    # pyproject (0.1.0) still matches the latest tag (0.1.0) -> auto-bump path
    result = _run_decide_cli(tmp_path, "--resolved-version", "0.2.0")
    assert result.returncode == 0, result.stderr
    out = dict(line.split("=", 1) for line in result.stdout.strip().splitlines())
    assert out["new_version"] == "0.2.0"
    assert out["needs_pyproject_bump"] == "true"
    assert out["needs_release"] == "true"


def test_cli_writes_github_output_file(tmp_path):
    _init_repo(tmp_path, version="0.1.0")
    output_file = tmp_path / "gh_output.txt"
    result = _run_decide_cli(tmp_path, "--github-output", str(output_file))
    assert result.returncode == 0, result.stderr
    content = output_file.read_text(encoding="utf-8")
    assert "new_version=0.1.0" in content


def test_tag_exists_true_and_false(tmp_path):
    _init_repo(tmp_path, version="0.1.0")
    assert tag_exists("v0.1.0", git_dir=tmp_path) is False
    subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, check=True)
    assert tag_exists("v0.1.0", git_dir=tmp_path) is True
