"""Typed QC domain model.

qc-skill never returns a bare boolean or an untyped dict as its top-level
result. Every response is built from these four concepts (STEP 1 of the
task spec):

  QCMeasurement - a fact that was actually measured (or explicitly marked
                  ``estimated`` when derived rather than directly reported).
                  Measurements are never rewritten by rule evaluation.
  Rule          - (see rules.py) a typed, caller-supplied expectation
                  against which a measurement is evaluated. Never baked
                  into this module as a hard-coded constant.
  QCFinding     - the *result* of evaluating a rule against a measurement:
                  a specific, evidenced thing that was detected.
  QCCheck       - one named check (e.g. "video.resolution_matches_expected"),
                  referencing the measurement(s) and finding(s) that back
                  its PASS/WARN/FAIL/UNKNOWN verdict.
  QCReport      - the top-level container: all checks, all measurements,
                  all findings, plus an aggregate ``overall_status`` and
                  provenance.

PASS/WARN/FAIL/UNKNOWN (STEP 2): UNKNOWN means the check could not be
performed (dependency unavailable, required measurement missing, decode
failed) - it is never collapsed into PASS, and it is never conflated with
FAIL (a confirmed violation). See ``QCStatus.aggregate``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class QCStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"

    @staticmethod
    def _rank(status: "QCStatus") -> int:
        # Worst-wins aggregation order. FAIL is a confirmed violation and
        # always dominates. UNKNOWN ("we could not verify this") is treated
        # as more significant than a confirmed WARN, because an unverified
        # check is strictly less informative than one that ran and only
        # produced a minor finding - it must never be hidden behind a WARN.
        return {
            QCStatus.PASS: 0,
            QCStatus.WARN: 1,
            QCStatus.UNKNOWN: 2,
            QCStatus.FAIL: 3,
        }[status]

    @classmethod
    def aggregate(cls, statuses: List["QCStatus"]) -> "QCStatus":
        if not statuses:
            return cls.UNKNOWN
        return max(statuses, key=cls._rank)


class FindingSeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass
class QCMeasurement:
    """A fact obtained from the media itself. Never a judgment."""

    id: str
    category: str  # video | audio | subtitle | delivery | container
    name: str
    value: Any
    unit: Optional[str] = None
    stream: Optional[int] = None
    source: str = "OBSERVED"  # e.g. "ffprobe", "ffmpeg:ebur128", "ffmpeg:silencedetect"
    estimated: bool = False  # true when derived rather than directly reported
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "category": self.category,
            "name": self.name,
            "value": self.value,
            "source": self.source,
            "estimated": self.estimated,
        }
        if self.unit is not None:
            d["unit"] = self.unit
        if self.stream is not None:
            d["stream"] = self.stream
        if self.notes is not None:
            d["notes"] = self.notes
        return d


@dataclass
class QCFinding:
    """The output of evaluating a Rule against one or more Measurements."""

    code: str
    severity: FindingSeverity
    message: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[float] = None
    stream: Optional[int] = None
    measurement_ids: List[str] = field(default_factory=list)
    rule_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "evidence": self.evidence,
        }
        if self.timestamp is not None:
            d["timestamp"] = self.timestamp
        if self.stream is not None:
            d["stream"] = self.stream
        if self.measurement_ids:
            d["measurement_ids"] = self.measurement_ids
        if self.rule_id is not None:
            d["rule_id"] = self.rule_id
        return d


@dataclass
class QCCheck:
    """One named check: a verdict, backed by specific measurements/findings."""

    check_id: str
    category: str
    status: QCStatus
    measurement_ids: List[str] = field(default_factory=list)
    finding_codes: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    reason: Optional[str] = None  # required when status == UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "check_id": self.check_id,
            "category": self.category,
            "status": self.status.value,
            "measurement_ids": self.measurement_ids,
            "finding_codes": self.finding_codes,
            "evidence": self.evidence,
        }
        if self.reason is not None:
            d["reason"] = self.reason
        return d


@dataclass
class QCReport:
    id: str
    version: str
    operation: str  # inspect | check | validate
    kind: str  # video | audio | subtitle | delivery
    input: Dict[str, Any]
    checks: List[QCCheck] = field(default_factory=list)
    measurements: List[QCMeasurement] = field(default_factory=list)
    findings: List[QCFinding] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)

    @property
    def overall_status(self) -> QCStatus:
        if not self.checks:
            return QCStatus.UNKNOWN
        return QCStatus.aggregate([c.status for c in self.checks])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "operation": self.operation,
            "kind": self.kind,
            "input": self.input,
            "overall_status": self.overall_status.value,
            "checks": [c.to_dict() for c in self.checks],
            "measurements": [m.to_dict() for m in self.measurements],
            "findings": [f.to_dict() for f in self.findings],
            "provenance": self.provenance,
        }
