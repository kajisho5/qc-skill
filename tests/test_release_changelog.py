"""scripts/release_changelog.py - CHANGELOG.md generation from `git log`
directly (never from a `${{ }}`-interpolated PR title/commit subject in a
shell run: block - see the module docstring for why)."""

import subprocess

from scripts.release_changelog import commit_subjects_since, prepend_to_changelog, render_entry


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)


def _commit(tmp_path, message, filename="file.txt"):
    (tmp_path / filename).write_text(message, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=tmp_path, check=True)


def test_commit_subjects_since_none_returns_full_history(tmp_path):
    _init_repo(tmp_path)
    _commit(tmp_path, "first commit")
    _commit(tmp_path, "second commit")
    subjects = commit_subjects_since(None, git_dir=tmp_path)
    assert [s for s, _ in subjects] == ["second commit", "first commit"]


def test_commit_subjects_since_a_tag_only_includes_later_commits(tmp_path):
    _init_repo(tmp_path)
    _commit(tmp_path, "first commit")
    subprocess.run(["git", "tag", "v0.1.0"], cwd=tmp_path, check=True)
    _commit(tmp_path, "second commit")
    _commit(tmp_path, "third commit")
    subjects = commit_subjects_since("v0.1.0", git_dir=tmp_path)
    assert [s for s, _ in subjects] == ["third commit", "second commit"]


def test_commit_subjects_since_handles_shell_metacharacters_safely(tmp_path):
    # The exact injection shape this script exists to make harmless: a
    # commit subject containing shell metacharacters must come through as
    # inert text, never be executed.
    _init_repo(tmp_path)
    dangerous = '"; echo pwned; #'
    _commit(tmp_path, dangerous)
    subjects = commit_subjects_since(None, git_dir=tmp_path)
    assert subjects[0][0] == dangerous
    assert not (tmp_path / "pwned").exists()


def test_render_entry_with_commits():
    text = render_entry("1.2.3", "2026-01-01", [("Add feature X", "abc1234"), ("Fix bug Y", "def5678")])
    assert text.startswith("## v1.2.3 - 2026-01-01")
    assert "- Add feature X (abc1234)" in text
    assert "- Fix bug Y (def5678)" in text


def test_render_entry_with_no_commits():
    text = render_entry("1.0.0", "2026-01-01", [])
    assert "No changes recorded." in text


def test_prepend_to_changelog_creates_new_file(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    entry = render_entry("1.0.0", "2026-01-01", [("Initial release", "abc1234")])
    prepend_to_changelog(changelog, entry)
    content = changelog.read_text(encoding="utf-8")
    assert content.startswith("# Changelog")
    assert "## v1.0.0 - 2026-01-01" in content
    assert "Initial release" in content


def test_prepend_to_changelog_puts_newest_entry_first(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    entry1 = render_entry("1.0.0", "2026-01-01", [("First release", "aaa1111")])
    prepend_to_changelog(changelog, entry1)
    entry2 = render_entry("1.1.0", "2026-02-01", [("Second release", "bbb2222")])
    prepend_to_changelog(changelog, entry2)
    content = changelog.read_text(encoding="utf-8")
    assert content.index("v1.1.0") < content.index("v1.0.0")
    assert "First release" in content
    assert "Second release" in content


def test_prepend_to_changelog_produces_valid_markdown_heading_spacing(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    entry = render_entry("1.0.0", "2026-01-01", [("Initial release", "abc1234")])
    prepend_to_changelog(changelog, entry)
    lines = changelog.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Changelog"
    # no more than one blank line between the top header and the first entry
    assert not (lines[1] == "" and lines[2] == "")
