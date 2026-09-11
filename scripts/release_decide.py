"""Decide the next release version for .github/workflows/release.yml.

Pure decision logic (no git/network I/O) so it can be unit tested
directly; the CLI wrapper at the bottom does the I/O (reading
pyproject.toml, querying git tags) and is what the workflow actually
calls.

Rule (see docs/decisions.md or the release.yml comments for the
rationale): auto-bump only fires when the current pyproject.toml version
still matches the latest git tag - i.e. nobody has manually bumped the
version since the last release. If they differ (including "no tag exists
yet", which trivially differs from any real version), the current
pyproject.toml version is respected as-is and never overwritten.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .release_version import read_version


@dataclass(frozen=True)
class ReleaseDecision:
    new_version: str
    needs_pyproject_bump: bool  # was the version auto-resolved (must be written to pyproject.toml)?


def decide_release(
    current_version: str, latest_tag_version: Optional[str], resolved_version: Optional[str]
) -> ReleaseDecision:
    if latest_tag_version is not None and current_version == latest_tag_version:
        if not resolved_version:
            raise ValueError(
                "current version matches the latest tag (no manual bump) but no "
                "resolved_version was supplied - the auto-bump step must run first"
            )
        return ReleaseDecision(new_version=resolved_version, needs_pyproject_bump=True)
    # Either no tag exists yet (first-ever release) or the version was
    # already bumped manually since the last tag - respect it as-is.
    return ReleaseDecision(new_version=current_version, needs_pyproject_bump=False)


def latest_tag_version(git_dir: Path = Path(".")) -> Optional[str]:
    result = subprocess.run(
        ["git", "tag", "--list", "v[0-9]*", "--sort=-v:refname"],
        cwd=git_dir, capture_output=True, text=True, check=True,
    )
    tags = [line for line in result.stdout.splitlines() if line]
    if not tags:
        return None
    return tags[0][1:]  # strip the leading "v"


def tag_exists(tag: str, git_dir: Path = Path(".")) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        cwd=git_dir, capture_output=True, text=True,
    )
    return result.returncode == 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", default="pyproject.toml", type=Path)
    parser.add_argument("--resolved-version", default=None, help="release-drafter's resolved_version output, if the auto-bump step ran")
    parser.add_argument("--github-output", default=None, type=Path, help="path to append GITHUB_OUTPUT-style key=value lines to")
    args = parser.parse_args(argv)

    current = read_version(args.pyproject)
    latest_tag = latest_tag_version()
    decision = decide_release(current, latest_tag, args.resolved_version)
    tag = f"v{decision.new_version}"
    already_released = tag_exists(tag)

    lines = [
        f"new_version={decision.new_version}",
        f"tag={tag}",
        f"needs_pyproject_bump={'true' if decision.needs_pyproject_bump else 'false'}",
        f"needs_release={'false' if already_released else 'true'}",
    ]
    output = "\n".join(lines)
    print(output)
    if args.github_output is not None:
        with open(args.github_output, "a", encoding="utf-8") as fh:
            fh.write(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
