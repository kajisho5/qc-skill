"""qc-skill: deterministic media quality-control / validation.

Responsibility boundary (see docs/architecture.md):
  qc-skill measures and checks. It never decides whether a video may be
  published, re-rendered, or blocked. That decision belongs to
  video-production-agent.
"""

SKILL_ID = "qc"
PACKAGE_NAME = "qc-skill"
VERSION = "0.1.0"
CONTRACT_VERSION = "1"

__all__ = ["SKILL_ID", "PACKAGE_NAME", "VERSION", "CONTRACT_VERSION"]
