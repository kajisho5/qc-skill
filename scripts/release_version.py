"""Read/bump the `[project] version` field in pyproject.toml.

Deliberately dependency-free (regex, not a TOML parser) because this only
ever has to handle this repo's own single, simple `version = "X.Y.Z"`
line - pulling in tomli/tomllib for one field is not worth it, and
tomllib isn't available before Python 3.11 while this project still
supports 3.9.

Used by .github/workflows/release.yml; also directly testable/runnable
standalone (see tests/scripts/test_release_version.py).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_VERSION_LINE_RE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"\s*$')
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def read_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text(encoding="utf-8")
    match = _VERSION_LINE_RE.search(text)
    if match is None:
        raise ValueError(f"no `version = \"...\"` line found in {pyproject_path}")
    return match.group(1)


def write_version(pyproject_path: Path, new_version: str) -> None:
    text = pyproject_path.read_text(encoding="utf-8")
    new_text, count = _VERSION_LINE_RE.subn(f'version = "{new_version}"', text, count=1)
    if count != 1:
        raise ValueError(f"no `version = \"...\"` line found in {pyproject_path}")
    pyproject_path.write_text(new_text, encoding="utf-8")


def parse_semver(version: str) -> tuple:
    match = _SEMVER_RE.match(version)
    if match is None:
        raise ValueError(f"not a plain X.Y.Z semver string: {version!r}")
    return tuple(int(part) for part in match.groups())


def bump_semver(version: str, level: str) -> str:
    major, minor, patch = parse_semver(version)
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    if level == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump level: {level!r} (expected major/minor/patch)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", default="pyproject.toml", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("get", help="print the current version")

    bump = sub.add_parser("bump", help="bump the version by one semver level and write it back")
    bump.add_argument("level", choices=["major", "minor", "patch"])

    set_ = sub.add_parser("set", help="write an exact version (e.g. one release-drafter already resolved)")
    set_.add_argument("version")

    args = parser.parse_args(argv)

    if args.command == "get":
        print(read_version(args.pyproject))
        return 0

    if args.command == "bump":
        current = read_version(args.pyproject)
        new_version = bump_semver(current, args.level)
        write_version(args.pyproject, new_version)
        print(new_version)
        return 0

    if args.command == "set":
        parse_semver(args.version)  # validate shape before writing
        write_version(args.pyproject, args.version)
        print(args.version)
        return 0

    return 1  # pragma: no cover - argparse `required=True` prevents this


if __name__ == "__main__":
    sys.exit(main())
