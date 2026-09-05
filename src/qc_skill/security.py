"""Path validation boundary (PathPolicy).

qc-skill reads arbitrary media supplied by a caller and, for some
operations, writes a report file. Both directions are constrained here so
that no request can escape its declared roots, regardless of ``..``
segments, symlinks, or platform-specific path quirks.

Design notes (see docs/security.md):
  * Raw strings are screened for traversal *before* any filesystem
    resolution happens (``_has_traversal``), so a rejected path is rejected
    for the reason a human would expect, not for whatever a resolved path
    happens to collide with.
  * Containment checks always compare *resolved* (symlinks followed)
    absolute paths using path-component comparison (``Path.relative_to``),
    never string prefix comparison - "/w/media" must not match
    "/w/media_evil".
  * Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9) and
    control characters are rejected in output filenames so a report can be
    written safely on any of the three supported platforms.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PureWindowsPath
from typing import List, Optional, Sequence

from .errors import error

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")

MAX_PATH_LENGTH = 4096
MAX_NAME_LENGTH = 255


def _has_traversal(raw: str) -> bool:
    """Reject '..' path segments in the *raw*, unresolved string.

    Checked with both POSIX and Windows segment splitting so a request
    processed on Linux still rejects a Windows-style ``..\\`` traversal
    attempt embedded in a string (defense in depth; behavior must not
    depend on which OS happens to run the check).
    """

    for part in re.split(r"[\\/]", raw):
        if part == "..":
            return True
    return False


def _check_path_string(raw: str, *, code: str) -> None:
    if not isinstance(raw, str) or not raw:
        raise error(code, "path must be a non-empty string", path=raw)
    if "\x00" in raw:
        raise error(code, "path contains a NUL byte", path=raw)
    if _CONTROL_CHARS.search(raw):
        raise error(code, "path contains control characters", path=raw)
    if len(raw) > MAX_PATH_LENGTH:
        raise error(code, "path exceeds maximum length", path=raw, max_length=MAX_PATH_LENGTH)


def check_filename(name: str, *, code: str = "OUTPUT_ERROR") -> None:
    """Validate a single filename component for cross-platform safety."""

    if not name or name in (".", ".."):
        raise error(code, "invalid filename", name=name)
    if len(name) > MAX_NAME_LENGTH:
        raise error(code, "filename exceeds maximum length", name=name, max_length=MAX_NAME_LENGTH)
    if _CONTROL_CHARS.search(name):
        raise error(code, "filename contains control characters", name=name)
    if name.startswith("-"):
        raise error(code, "filename must not look like an option flag", name=name)
    if name.endswith(" ") or name.endswith("."):
        raise error(code, "filename must not end with a space or dot (Windows-unsafe)", name=name)
    stem = PureWindowsPath(name).stem.upper()
    if stem in _WINDOWS_RESERVED:
        raise error(code, "filename is a reserved device name on Windows", name=name)
    for ch in '<>:"|?*':
        if ch in name:
            raise error(code, "filename contains a character reserved on Windows", name=name, character=ch)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class PathPolicy:
    """Confines input reads and output writes to declared roots.

    ``allowed_input_roots``: when given, every resolved input path must be
    a descendant of one of these roots (``None`` means "any readable
    regular file", matching the rest of the ecosystem's default posture -
    see docs/security.md).

    ``workspace``: the root every resolved *output/report* path must stay
    inside. Defaults to the current working directory.
    """

    def __init__(
        self,
        workspace: Optional[str] = None,
        allowed_input_roots: Optional[Sequence[str]] = None,
    ) -> None:
        self.workspace = Path(workspace or os.getcwd()).resolve()
        self.allowed_input_roots: Optional[List[Path]] = (
            [Path(r).resolve() for r in allowed_input_roots] if allowed_input_roots else None
        )

    def resolve_input(self, raw_path: str, *, must_exist: bool = True) -> Optional[Path]:
        """Resolve and validate one input path.

        ``must_exist=False`` (used only for ``kind: "delivery_package"``
        artifacts, where "this artifact is missing" is itself a
        graceful, reportable fact rather than a request-level error)
        returns ``None`` instead of raising when the path simply does not
        exist. Every other validation - traversal, control characters,
        not-a-regular-file, outside the allowed input roots - still
        raises exactly as it does today, because those are never a
        legitimate "the artifact is absent" outcome.
        """

        _check_path_string(raw_path, code="PATH_NOT_ALLOWED")
        if _has_traversal(raw_path):
            raise error("PATH_NOT_ALLOWED", "path traversal ('..') is not allowed", path=raw_path)

        candidate = Path(raw_path)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError:
            if not must_exist:
                return None
            raise error("MISSING_INPUT", "input file does not exist", path=raw_path)
        except OSError as exc:
            raise error("INVALID_INPUT", f"could not resolve input path: {exc}", path=raw_path)

        if not resolved.is_file():
            raise error("INVALID_INPUT", "input path is not a regular file", path=raw_path)

        if self.allowed_input_roots is not None:
            if not any(_under(resolved, root) for root in self.allowed_input_roots):
                raise error(
                    "PATH_NOT_ALLOWED",
                    "input path is outside the allowed input roots",
                    path=str(resolved),
                    allowed_roots=[str(r) for r in self.allowed_input_roots],
                )
        return resolved

    def resolve_output(self, raw_path: str) -> Path:
        _check_path_string(raw_path, code="OUTPUT_ERROR")
        if _has_traversal(raw_path):
            raise error("OUTPUT_ERROR", "path traversal ('..') is not allowed", path=raw_path)
        if os.path.isabs(raw_path):
            raise error("OUTPUT_ERROR", "absolute output paths are not allowed", path=raw_path)

        check_filename(Path(raw_path).name, code="OUTPUT_ERROR")

        candidate = (self.workspace / raw_path)

        # Resolve the parent directory (following any symlinks that exist
        # today), then re-append the leaf name, so a symlink cannot be used
        # to smuggle the final component outside the workspace.
        parent_resolved = self._resolve_deepest_existing_ancestor(candidate.parent)
        final = parent_resolved / candidate.name

        if not _under(final, self.workspace):
            raise error(
                "OUTPUT_ERROR",
                "output path is outside the workspace",
                path=str(final),
                workspace=str(self.workspace),
            )
        return final

    def _resolve_deepest_existing_ancestor(self, path: Path) -> Path:
        path = (self.workspace / path) if not path.is_absolute() else path
        parts_to_append: List[str] = []
        current = path
        while True:
            try:
                return current.resolve(strict=True).joinpath(*reversed(parts_to_append))
            except FileNotFoundError:
                if current.parent == current:
                    return current.joinpath(*reversed(parts_to_append))
                parts_to_append.append(current.name)
                current = current.parent
