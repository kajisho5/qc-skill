"""Report reuse/cache (STEP 21 of the task spec).

Same input content + same effective request parameters + same skill
version + same engine (ffmpeg/ffprobe) version can safely reuse a
previously computed QCReport. Every read re-validates the stored report
against its own recorded hash before returning it as a hit - a corrupted
or hand-edited cache file is never returned as a successful reuse.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from .canonical import stable_hash

CACHE_FORMAT = "qc-cache/1"


class QCReportCache:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path_for(self, key: str) -> Path:
        return self.directory / key[:2] / f"{key}.json"

    def get(self, key: str, expected_metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                entry = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None

        if entry.get("format") != CACHE_FORMAT:
            return None
        if entry.get("key") != key:
            return None
        stored_metadata = entry.get("metadata", {})
        for field_name, expected_value in expected_metadata.items():
            if stored_metadata.get(field_name) != expected_value:
                return None

        report = entry.get("report")
        if report is None:
            return None
        if entry.get("result_hash") != stable_hash(report):
            self._invalidate(path)
            return None

        return report

    def set(self, key: str, metadata: Dict[str, Any], report: Dict[str, Any]) -> None:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "format": CACHE_FORMAT,
            "key": key,
            "metadata": metadata,
            "result_hash": stable_hash(report),
            "report": report,
        }
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entry, fh, sort_keys=True, separators=(",", ":"))
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _invalidate(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass
