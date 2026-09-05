from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.fixtures.generate import build_all


def _require(executable: str) -> None:
    if shutil.which(executable) is None:
        pytest.fail(
            f"{executable} is required to run this test suite (real-media E2E is not optional - "
            "see STEP 22 of the task spec). Install ffmpeg/ffprobe and retry."
        )


@pytest.fixture(scope="session")
def media(tmp_path_factory) -> dict:
    _require("ffmpeg")
    _require("ffprobe")
    out_dir = tmp_path_factory.mktemp("qc-fixtures")
    return build_all(out_dir)


@pytest.fixture()
def workspace(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path
