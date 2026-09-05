"""Structured error model for qc-skill.

Every error the skill can raise as an *execution* failure (as opposed to a
QC verdict of FAIL, which is a normal, successful result - see
docs/architecture.md "PASS/WARN/FAIL/UNKNOWN vs execution errors") is one of
the codes below. Codes and exit numbers follow the convention shared by the
sibling skills (media-analysis-skill, audio-production-skill): small integer
exit codes starting at 2, one dataclass-like exception per code, each
carrying a machine-readable ``code`` and a human ``message``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# code -> (exit_code, retryable)
ERROR_CODES = {
    "INVALID_REQUEST": (2, False),
    "INVALID_INPUT": (3, False),
    "PATH_NOT_ALLOWED": (4, False),
    "UNSUPPORTED_OPERATION": (5, False),
    "UNSUPPORTED_FORMAT": (6, False),
    "MISSING_INPUT": (7, False),
    "INVALID_TIME_RANGE": (8, False),
    "DEPENDENCY_ERROR": (9, False),
    "OUTPUT_ERROR": (10, False),
    "VALIDATION_ERROR": (11, False),
    "TOOL_ERROR": (12, True),
    "CANCELLED": (13, True),
    "INTERNAL_ERROR": (14, False),
}


class QCError(Exception):
    """Base class for all qc-skill execution errors (not QC verdicts)."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return ERROR_CODES[self.code][0]

    @property
    def retryable(self) -> bool:
        return ERROR_CODES[self.code][1]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


def error(code: str, message: str, **details: Any) -> QCError:
    return QCError(code, message, details or None)
