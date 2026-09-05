"""Request parsing/validation (STEP 11/15 of the task spec).

The request document is the only caller-controlled input to a ``run``
invocation. It is validated against an explicit allow-list, and a fixed
set of keys is rejected outright regardless of type - a caller can never
smuggle a command, argv, shell flag, filter string, executable path, or
environment override through the request (STEP 15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import error
from .rules import (
    ArtifactDependencyRule,
    ArtifactDurationConsistencyRule,
    AudioRule,
    CrossArtifactRule,
    DeliveryArtifactRule,
    DeliveryPackageRule,
    DeliveryRule,
    SourceCue,
    SubtitleRule,
    TimelineIntegrityRule,
    TimelineSegment,
    VideoRule,
)

REQUEST_SCHEMA = "qc/request@1"

ALLOWED_TOP_LEVEL_KEYS = {
    "schema", "request_id", "operation", "kind", "input", "subtitle", "reference_video",
    "artifacts", "parameters", "rules", "cache_policy", "timeout",
}

FORBIDDEN_KEYS = {
    "command", "commands", "argv", "args", "shell", "cmd", "cmdline",
    "exec", "executable", "filter", "filter_complex", "env", "environment",
}

VALID_OPERATIONS = {"inspect", "check", "validate"}
VALID_KINDS = {"video", "audio", "subtitle", "delivery", "delivery_package"}
VALID_CACHE_POLICIES = {"use", "bypass", "only"}
VALID_ARTIFACT_TYPES = {"video", "audio", "subtitle", "thumbnail", "metadata", "other"}

_ALLOWED_PARAMETER_KEYS = {
    "black_min_duration_sec", "black_pixel_threshold",
    "freeze_noise_db", "freeze_min_duration_sec",
    "silence_threshold_db", "silence_min_duration_sec", "clipping_threshold_dbfs",
    "max_line_length", "max_cue_duration_sec", "max_gap_sec",
}


def _find_forbidden(doc: Any, path: str = "") -> Optional[str]:
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in FORBIDDEN_KEYS:
                return f"{path}.{k}" if path else k
            found = _find_forbidden(v, f"{path}.{k}" if path else k)
            if found:
                return found
    elif isinstance(doc, list):
        for i, item in enumerate(doc):
            found = _find_forbidden(item, f"{path}[{i}]")
            if found:
                return found
    return None


@dataclass
class DeliveryArtifact:
    """One named file inside a ``kind: "delivery_package"`` request.

    This, paired with ``DeliveryPackageRule`` (rules.py), is the typed
    "DeliveryQC specification" boundary object: qc-skill accepts exactly
    this shape and never a ``ProductionPlan``/agent-side type directly
    (ADR-010). ``fingerprint`` is deliberately not an accepted field here
    - it is always computed by qc-skill itself from the resolved file,
    never trusted from the caller (mirrors ADR-008's reuse-revalidation
    stance and the agent adapter's own re-hashing behavior).
    """

    artifact_id: str
    artifact_type: str
    path: str


@dataclass
class Request:
    operation: str
    kind: str
    input: Optional[str] = None
    subtitle: Optional[str] = None
    reference_video: Optional[str] = None
    artifacts: List["DeliveryArtifact"] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    video_rule: Optional[VideoRule] = None
    audio_rule: Optional[AudioRule] = None
    subtitle_rule: Optional[SubtitleRule] = None
    delivery_rule: Optional[DeliveryRule] = None
    delivery_package_rule: Optional[DeliveryPackageRule] = None
    cache_policy: str = "use"
    timeout: Optional[float] = None
    request_id: Optional[str] = None


def _build_rule(cls, data: Optional[Dict[str, Any]]):
    if data is None:
        return None
    if not isinstance(data, dict):
        raise error("INVALID_REQUEST", f"rule payload for {cls.__name__} must be an object")
    valid_fields = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    unknown = set(data) - valid_fields
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for {cls.__name__}: {sorted(unknown)}")
    kwargs = dict(data)
    if "video" in kwargs and kwargs["video"] is not None:
        kwargs["video"] = _build_rule(VideoRule, kwargs["video"])
    if "audio" in kwargs and kwargs["audio"] is not None:
        kwargs["audio"] = _build_rule(AudioRule, kwargs["audio"])
    if "subtitle" in kwargs and kwargs["subtitle"] is not None:
        kwargs["subtitle"] = _build_rule(SubtitleRule, kwargs["subtitle"])
    if "timeline_integrity" in kwargs and kwargs["timeline_integrity"] is not None:
        kwargs["timeline_integrity"] = _build_timeline_integrity_rule(kwargs["timeline_integrity"])
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise error("INVALID_REQUEST", f"invalid rule payload for {cls.__name__}: {exc}")


def _number(value: Any, *, field_name: str, allow_negative: bool = True) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise error("INVALID_REQUEST", f"{field_name} must be a number")
    if not allow_negative and value < 0:
        raise error("INVALID_REQUEST", f"{field_name} must not be negative")
    return float(value)


def _build_timeline_segment(item: Any) -> TimelineSegment:
    if not isinstance(item, dict):
        raise error("INVALID_REQUEST", "each entry in timeline_integrity.timeline must be an object")
    unknown = set(item) - {"source_start", "source_end", "delivery_start", "speed"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for a timeline segment: {sorted(unknown)}")
    for key in ("source_start", "source_end", "delivery_start"):
        if key not in item:
            raise error("INVALID_REQUEST", f"timeline segment is missing required field {key!r}")
    speed = _number(item.get("speed", 1.0), field_name="timeline segment.speed", allow_negative=False)
    if speed == 0:
        raise error("INVALID_REQUEST", "timeline segment.speed must be greater than zero")
    return TimelineSegment(
        source_start=_number(item["source_start"], field_name="timeline segment.source_start"),
        source_end=_number(item["source_end"], field_name="timeline segment.source_end"),
        delivery_start=_number(item["delivery_start"], field_name="timeline segment.delivery_start"),
        speed=speed,
    )


def _build_source_cue(item: Any) -> SourceCue:
    if not isinstance(item, dict):
        raise error("INVALID_REQUEST", "each entry in timeline_integrity.source_cues must be an object")
    unknown = set(item) - {"start", "end"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for a source cue: {sorted(unknown)}")
    if "start" not in item or "end" not in item:
        raise error("INVALID_REQUEST", "a source cue requires both 'start' and 'end'")
    return SourceCue(
        start=_number(item["start"], field_name="source cue.start"),
        end=_number(item["end"], field_name="source cue.end"),
    )


def _build_timeline_integrity_rule(data: Any) -> TimelineIntegrityRule:
    if not isinstance(data, dict):
        raise error("INVALID_REQUEST", "rule payload for TimelineIntegrityRule must be an object")
    unknown = set(data) - {"timeline", "source_cues", "tolerance_sec"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for TimelineIntegrityRule: {sorted(unknown)}")
    timeline_data = data.get("timeline", [])
    if not isinstance(timeline_data, list):
        raise error("INVALID_REQUEST", "TimelineIntegrityRule.timeline must be a list")
    source_cues_data = data.get("source_cues", [])
    if not isinstance(source_cues_data, list):
        raise error("INVALID_REQUEST", "TimelineIntegrityRule.source_cues must be a list")
    tolerance_sec = _number(data.get("tolerance_sec", 0.1), field_name="TimelineIntegrityRule.tolerance_sec", allow_negative=False)
    return TimelineIntegrityRule(
        timeline=[_build_timeline_segment(s) for s in timeline_data],
        source_cues=[_build_source_cue(c) for c in source_cues_data],
        tolerance_sec=tolerance_sec,
    )


def _build_delivery_artifact_rule(data: Any) -> DeliveryArtifactRule:
    if not isinstance(data, dict):
        raise error("INVALID_REQUEST", "each entry in rules.delivery_package.artifacts must be an object")
    valid_fields = {f.name for f in DeliveryArtifactRule.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    unknown = set(data) - valid_fields
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for DeliveryArtifactRule: {sorted(unknown)}")
    if not isinstance(data.get("artifact_id"), str) or not data.get("artifact_id"):
        raise error("INVALID_REQUEST", "DeliveryArtifactRule.artifact_id must be a non-empty string")
    kwargs = dict(data)
    if kwargs.get("video") is not None:
        kwargs["video"] = _build_rule(VideoRule, kwargs["video"])
    if kwargs.get("audio") is not None:
        kwargs["audio"] = _build_rule(AudioRule, kwargs["audio"])
    if kwargs.get("subtitle") is not None:
        kwargs["subtitle"] = _build_rule(SubtitleRule, kwargs["subtitle"])
    try:
        return DeliveryArtifactRule(**kwargs)
    except TypeError as exc:
        raise error("INVALID_REQUEST", f"invalid DeliveryArtifactRule payload: {exc}")


def _build_duration_consistency_rule(data: Any) -> ArtifactDurationConsistencyRule:
    if not isinstance(data, dict):
        raise error("INVALID_REQUEST", "each entry in cross_artifact.duration_consistency must be an object")
    unknown = set(data) - {"artifact_ids", "max_delta_sec"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for duration_consistency rule: {sorted(unknown)}")
    artifact_ids = data.get("artifact_ids")
    if not isinstance(artifact_ids, list) or len(artifact_ids) < 2 or not all(isinstance(a, str) and a for a in artifact_ids):
        raise error("INVALID_REQUEST", "duration_consistency.artifact_ids must be a list of at least 2 non-empty strings")
    max_delta_sec = data.get("max_delta_sec")
    if not isinstance(max_delta_sec, (int, float)) or isinstance(max_delta_sec, bool) or max_delta_sec < 0:
        raise error("INVALID_REQUEST", "duration_consistency.max_delta_sec must be a non-negative number")
    return ArtifactDurationConsistencyRule(artifact_ids=list(artifact_ids), max_delta_sec=float(max_delta_sec))


def _build_dependency_rule(data: Any) -> ArtifactDependencyRule:
    if not isinstance(data, dict):
        raise error("INVALID_REQUEST", "each entry in cross_artifact.dependencies must be an object")
    unknown = set(data) - {"artifact_id", "requires_artifact_id"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for dependency rule: {sorted(unknown)}")
    artifact_id = data.get("artifact_id")
    requires_artifact_id = data.get("requires_artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise error("INVALID_REQUEST", "dependency.artifact_id must be a non-empty string")
    if not isinstance(requires_artifact_id, str) or not requires_artifact_id:
        raise error("INVALID_REQUEST", "dependency.requires_artifact_id must be a non-empty string")
    return ArtifactDependencyRule(artifact_id=artifact_id, requires_artifact_id=requires_artifact_id)


def _build_cross_artifact_rule(data: Any) -> Optional[CrossArtifactRule]:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise error("INVALID_REQUEST", "rule payload for CrossArtifactRule must be an object")
    unknown = set(data) - {"duration_consistency", "dependencies"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for CrossArtifactRule: {sorted(unknown)}")
    duration_data = data.get("duration_consistency", [])
    if not isinstance(duration_data, list):
        raise error("INVALID_REQUEST", "cross_artifact.duration_consistency must be a list")
    dependency_data = data.get("dependencies", [])
    if not isinstance(dependency_data, list):
        raise error("INVALID_REQUEST", "cross_artifact.dependencies must be a list")
    return CrossArtifactRule(
        duration_consistency=[_build_duration_consistency_rule(d) for d in duration_data],
        dependencies=[_build_dependency_rule(d) for d in dependency_data],
    )


def _build_delivery_package_rule(data: Optional[Dict[str, Any]]) -> Optional[DeliveryPackageRule]:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise error("INVALID_REQUEST", "rule payload for DeliveryPackageRule must be an object")
    unknown = set(data) - {"artifacts", "cross_artifact"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields for DeliveryPackageRule: {sorted(unknown)}")
    artifacts_data = data.get("artifacts", [])
    if not isinstance(artifacts_data, list):
        raise error("INVALID_REQUEST", "DeliveryPackageRule.artifacts must be a list")
    artifact_rules = [_build_delivery_artifact_rule(a) for a in artifacts_data]
    ids = [a.artifact_id for a in artifact_rules]
    if len(ids) != len(set(ids)):
        raise error("INVALID_REQUEST", "rules.delivery_package.artifacts has duplicate artifact_id values")
    return DeliveryPackageRule(
        artifacts=artifact_rules,
        cross_artifact=_build_cross_artifact_rule(data.get("cross_artifact")),
    )


def _build_artifact(item: Any) -> DeliveryArtifact:
    if not isinstance(item, dict):
        raise error("INVALID_REQUEST", "each entry in request.artifacts must be an object")
    unknown = set(item) - {"artifact_id", "artifact_type", "path"}
    if unknown:
        raise error("INVALID_REQUEST", f"unknown fields in artifact entry: {sorted(unknown)}")
    artifact_id = item.get("artifact_id")
    artifact_type = item.get("artifact_type")
    path = item.get("path")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise error("INVALID_REQUEST", "artifact.artifact_id must be a non-empty string")
    if artifact_type not in VALID_ARTIFACT_TYPES:
        raise error("INVALID_REQUEST", f"artifact.artifact_type must be one of {sorted(VALID_ARTIFACT_TYPES)}", got=artifact_type)
    if not isinstance(path, str) or not path:
        raise error("INVALID_REQUEST", "artifact.path must be a non-empty string")
    return DeliveryArtifact(artifact_id=artifact_id, artifact_type=artifact_type, path=path)


def parse_request(doc: Any) -> Request:
    if not isinstance(doc, dict):
        raise error("INVALID_REQUEST", "request must be a JSON object")

    forbidden = _find_forbidden(doc)
    if forbidden:
        raise error("INVALID_REQUEST", f"request contains a forbidden key: {forbidden}")

    unknown = set(doc) - ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        raise error("INVALID_REQUEST", f"unknown top-level request keys: {sorted(unknown)}")

    operation = doc.get("operation")
    if operation not in VALID_OPERATIONS:
        raise error("UNSUPPORTED_OPERATION", f"operation must be one of {sorted(VALID_OPERATIONS)}", got=operation)

    kind = doc.get("kind")
    if kind not in VALID_KINDS:
        raise error("INVALID_REQUEST", f"kind must be one of {sorted(VALID_KINDS)}", got=kind)

    input_path = doc.get("input")
    if kind == "delivery_package":
        # There is no single primary artifact for a package (that is the
        # whole point of it) - file paths come from request.artifacts
        # instead, exactly as request.subtitle is separate from input
        # today for kind="delivery".
        if input_path is not None and (not isinstance(input_path, str) or not input_path):
            raise error("INVALID_REQUEST", "request.input must be a non-empty string when present")
    elif not isinstance(input_path, str) or not input_path:
        raise error("MISSING_INPUT", "request.input must be a non-empty string")

    artifacts_doc = doc.get("artifacts", [])
    if not isinstance(artifacts_doc, list):
        raise error("INVALID_REQUEST", "request.artifacts must be a list")
    artifacts = [_build_artifact(a) for a in artifacts_doc]
    artifact_ids = [a.artifact_id for a in artifacts]
    if len(artifact_ids) != len(set(artifact_ids)):
        raise error("INVALID_REQUEST", "request.artifacts has duplicate artifact_id values")
    if kind == "delivery_package" and not artifacts:
        raise error("INVALID_REQUEST", "kind=delivery_package requires a non-empty request.artifacts list")
    if kind != "delivery_package" and artifacts:
        raise error("INVALID_REQUEST", "request.artifacts is only accepted when kind=delivery_package")

    subtitle_path = doc.get("subtitle")
    if subtitle_path is not None and not isinstance(subtitle_path, str):
        raise error("INVALID_REQUEST", "request.subtitle must be a string when present")

    reference_video = doc.get("reference_video")
    if reference_video is not None and not isinstance(reference_video, str):
        raise error("INVALID_REQUEST", "request.reference_video must be a string when present")

    parameters = doc.get("parameters", {})
    if not isinstance(parameters, dict):
        raise error("INVALID_REQUEST", "request.parameters must be an object")
    unknown_params = set(parameters) - _ALLOWED_PARAMETER_KEYS
    if unknown_params:
        raise error("INVALID_REQUEST", f"unknown parameter keys: {sorted(unknown_params)}")

    rules_doc = doc.get("rules", {})
    if not isinstance(rules_doc, dict):
        raise error("INVALID_REQUEST", "request.rules must be an object")
    unknown_rule_keys = set(rules_doc) - {"video", "audio", "subtitle", "delivery", "delivery_package"}
    if unknown_rule_keys:
        raise error("INVALID_REQUEST", f"unknown rule sections: {sorted(unknown_rule_keys)}")

    cache_policy = doc.get("cache_policy", "use")
    if cache_policy not in VALID_CACHE_POLICIES:
        raise error("INVALID_REQUEST", f"cache_policy must be one of {sorted(VALID_CACHE_POLICIES)}", got=cache_policy)

    timeout = doc.get("timeout")
    if timeout is not None:
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise error("INVALID_TIME_RANGE", "request.timeout must be a positive number of seconds")

    request_id = doc.get("request_id")
    if request_id is not None and not isinstance(request_id, str):
        raise error("INVALID_REQUEST", "request.request_id must be a string when present")

    return Request(
        operation=operation,
        kind=kind,
        input=input_path,
        subtitle=subtitle_path,
        reference_video=reference_video,
        artifacts=artifacts,
        parameters=parameters,
        video_rule=_build_rule(VideoRule, rules_doc.get("video")),
        audio_rule=_build_rule(AudioRule, rules_doc.get("audio")),
        subtitle_rule=_build_rule(SubtitleRule, rules_doc.get("subtitle")),
        delivery_rule=_build_rule(DeliveryRule, rules_doc.get("delivery")),
        delivery_package_rule=_build_delivery_package_rule(rules_doc.get("delivery_package")),
        cache_policy=cache_policy,
        timeout=timeout,
        request_id=request_id,
    )
