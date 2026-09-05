"""Request parsing/validation (STEP 11/15 of the task spec).

The request document is the only caller-controlled input to a ``run``
invocation. It is validated against an explicit allow-list, and a fixed
set of keys is rejected outright regardless of type - a caller can never
smuggle a command, argv, shell flag, filter string, executable path, or
environment override through the request (STEP 15).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .errors import error
from .rules import AudioRule, DeliveryRule, SubtitleRule, VideoRule

REQUEST_SCHEMA = "qc/request@1"

ALLOWED_TOP_LEVEL_KEYS = {
    "schema", "request_id", "operation", "kind", "input", "subtitle", "reference_video",
    "parameters", "rules", "cache_policy", "timeout",
}

FORBIDDEN_KEYS = {
    "command", "commands", "argv", "args", "shell", "cmd", "cmdline",
    "exec", "executable", "filter", "filter_complex", "env", "environment",
}

VALID_OPERATIONS = {"inspect", "check", "validate"}
VALID_KINDS = {"video", "audio", "subtitle", "delivery"}
VALID_CACHE_POLICIES = {"use", "bypass", "only"}

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
class Request:
    operation: str
    kind: str
    input: str
    subtitle: Optional[str] = None
    reference_video: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    video_rule: Optional[VideoRule] = None
    audio_rule: Optional[AudioRule] = None
    subtitle_rule: Optional[SubtitleRule] = None
    delivery_rule: Optional[DeliveryRule] = None
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
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise error("INVALID_REQUEST", f"invalid rule payload for {cls.__name__}: {exc}")


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
    if not isinstance(input_path, str) or not input_path:
        raise error("MISSING_INPUT", "request.input must be a non-empty string")

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
    unknown_rule_keys = set(rules_doc) - {"video", "audio", "subtitle", "delivery"}
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
        parameters=parameters,
        video_rule=_build_rule(VideoRule, rules_doc.get("video")),
        audio_rule=_build_rule(AudioRule, rules_doc.get("audio")),
        subtitle_rule=_build_rule(SubtitleRule, rules_doc.get("subtitle")),
        delivery_rule=_build_rule(DeliveryRule, rules_doc.get("delivery")),
        cache_policy=cache_policy,
        timeout=timeout,
        request_id=request_id,
    )
