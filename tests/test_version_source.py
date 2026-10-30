"""Every version qc reports comes from pyproject.toml (ADR-020).

The release workflow bumps only pyproject.toml. These tests fail if any
reported version - the package constant, `contract --json`, the per-tool
specs inside it, or `doctor --json` - stops matching it, so a hard-coded
constant cannot silently drift behind a release again.
"""

import json
import subprocess
import sys
from pathlib import Path

from qc_skill import VERSION
from qc_skill.contract import doctor_report, skill_contract, tool_spec
from scripts.release_version import read_version

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_VERSION = read_version(REPO_ROOT / "pyproject.toml")
CLI = [sys.executable, "-m", "qc_skill.cli"]


def test_package_version_equals_pyproject_version():
    assert VERSION == PYPROJECT_VERSION


def test_contract_version_equals_pyproject_version():
    doc = skill_contract()
    assert doc["version"] == PYPROJECT_VERSION
    assert tool_spec("loudness", "audio")["version"] == PYPROJECT_VERSION


def test_doctor_version_equals_pyproject_version(tmp_path):
    assert doctor_report(str(tmp_path))["skill"]["version"] == PYPROJECT_VERSION


def test_cli_contract_json_reports_pyproject_version():
    # Run from outside the checkout so the answer cannot depend on the cwd.
    result = subprocess.run(CLI + ["contract", "--json"], capture_output=True, text=True, cwd=str(Path.home()))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["version"] == PYPROJECT_VERSION


def test_cli_doctor_json_reports_pyproject_version():
    result = subprocess.run(CLI + ["doctor", "--json"], capture_output=True, text=True, cwd=str(Path.home()))
    assert json.loads(result.stdout)["skill"]["version"] == PYPROJECT_VERSION
