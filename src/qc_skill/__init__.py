"""qc-skill: deterministic media quality-control / validation.

Responsibility boundary (see docs/architecture.md):
  qc-skill measures and checks. It never decides whether a video may be
  published, re-rendered, or blocked. That decision belongs to
  video-production-agent.
"""

SKILL_ID = "qc"
PACKAGE_NAME = "qc-skill"


def _version() -> str:
    """The package version, from one place: pyproject.toml (the only file
    the release workflow bumps - see ADR-020).

    From a source checkout (including an editable install, whose recorded
    metadata goes stale on the next release bump) it is read straight from
    this checkout's pyproject.toml; from an installed wheel, from the
    distribution metadata that was built from that same pyproject.toml.
    """
    import re
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.is_file():
        text = pyproject.read_text(encoding="utf-8")
        if re.search(r'^name\s*=\s*"qc-skill"\s*$', text, re.M):
            found = re.search(r'^version\s*=\s*"([^"]+)"\s*$', text, re.M)
            if found:
                return found.group(1)
    try:
        from importlib.metadata import PackageNotFoundError, version
    except ImportError:  # pragma: no cover - Python < 3.8 is not supported
        return "0.0.0+unknown"
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        return "0.0.0+unknown"


VERSION = _version()
CONTRACT_VERSION = "1"

__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION", "CONTRACT_VERSION"]
