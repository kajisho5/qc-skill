"""Orchestration: input -> measurement -> check -> finding -> report.

This is the one place that ties the pieces together (STEP 12/14 of the
task spec). It never decides whether media may ship; it only measures and
evaluates the rules it was explicitly given, and records provenance so the
resulting report is independently auditable.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import VERSION as SKILL_VERSION
from . import SKILL_ID
from .cache import QCReportCache
from .canonical import sha256_file, stable_hash
from .capabilities import CapabilitySet
from .errors import error
from .measurements.audio import analyze_audio, measure_audio_streams
from .measurements.subtitle import measure_subtitle
from .measurements.video import analyze_video_defects, measure_container, measure_video_streams
from .models import QCCheck, QCFinding, QCMeasurement, QCReport
from .probe import audio_streams, probe_media, video_streams
from .rules import AudioRule, SubtitleRule, evaluate_audio, evaluate_delivery_basics, evaluate_subtitle, evaluate_video
from .schemas import Request
from .security import PathPolicy

DEFAULT_PARAMETERS: Dict[str, Any] = {
    "black_min_duration_sec": 0.5,
    "black_pixel_threshold": 0.10,
    "freeze_noise_db": -60.0,
    "freeze_min_duration_sec": 1.0,
    "silence_threshold_db": -30.0,
    "silence_min_duration_sec": 0.5,
    "clipping_threshold_dbfs": -0.1,
    "max_line_length": 42,
    "max_cue_duration_sec": 7.0,
    "max_gap_sec": 5.0,
}


def _default_clock() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)


def now_iso(clock: Optional[Callable[[], _dt.datetime]] = None) -> str:
    dt = (clock or _default_clock)()
    return dt.replace(microsecond=0).isoformat() + "Z"


@dataclass
class ExecutionContext:
    path_policy: PathPolicy
    capabilities: CapabilitySet
    cache: Optional[QCReportCache] = None
    clock: Optional[Callable[[], _dt.datetime]] = None


def _measurement_map(measurements: List[QCMeasurement]) -> Dict[str, QCMeasurement]:
    return {m.id: m for m in measurements}


def _dedupe_measurements(measurements: List[QCMeasurement]) -> List[QCMeasurement]:
    """Delivery gathers video and audio metadata via two overlapping calls
    (each probes basic container/stream facts); keep the last, most
    detailed occurrence of each measurement id, preserving first-seen order.
    """

    order: List[str] = []
    by_id: Dict[str, QCMeasurement] = {}
    for m in measurements:
        if m.id not in by_id:
            order.append(m.id)
        by_id[m.id] = m
    return [by_id[i] for i in order]


def _gather_video_measurements(
    ctx: ExecutionContext, input_path: Path, params: Dict[str, Any]
) -> List[QCMeasurement]:
    probe_data = probe_media(ctx.capabilities.ffprobe_path, input_path)
    measurements = measure_container(probe_data, actual_size_bytes=input_path.stat().st_size)
    measurements += measure_video_streams(probe_data)
    measurements += measure_audio_streams(probe_data)

    v_streams = video_streams(probe_data)
    if v_streams and ctx.capabilities.ffmpeg_available:
        expected_frame_count = None
        fc_measurement = next((m for m in measurements if m.id == "video.frame_count"), None)
        if fc_measurement is not None and not fc_measurement.estimated:
            expected_frame_count = fc_measurement.value
        result = analyze_video_defects(
            ctx.capabilities.ffmpeg_path,
            input_path,
            v_streams[0]["index"],
            expected_frame_count,
            black_min_duration=params["black_min_duration_sec"],
            black_pixel_threshold=params["black_pixel_threshold"],
            freeze_noise_db=params["freeze_noise_db"],
            freeze_min_duration=params["freeze_min_duration_sec"],
        )
        measurements += result.measurements
    elif v_streams:
        measurements.append(
            QCMeasurement(
                "video.decode_error_count", "video", "decode_error_count", None,
                source="ffmpeg:decode", notes="not performed: ffmpeg is not available",
            )
        )
    return measurements


def _gather_audio_measurements(
    ctx: ExecutionContext, input_path: Path, params: Dict[str, Any]
) -> List[QCMeasurement]:
    probe_data = probe_media(ctx.capabilities.ffprobe_path, input_path)
    measurements = measure_container(probe_data, actual_size_bytes=input_path.stat().st_size)
    measurements += measure_audio_streams(probe_data)

    a_streams = audio_streams(probe_data)
    if a_streams and ctx.capabilities.ffmpeg_available:
        duration_m = next((m for m in measurements if m.id == "audio.duration_sec"), None)
        result = analyze_audio(
            ctx.capabilities.ffmpeg_path,
            input_path,
            a_streams[0]["index"],
            duration_m.value if duration_m else None,
            silence_threshold_db=params["silence_threshold_db"],
            silence_min_duration=params["silence_min_duration_sec"],
            clipping_threshold_db=params["clipping_threshold_dbfs"],
        )
        measurements += result.measurements
    elif a_streams:
        measurements.append(
            QCMeasurement(
                "audio.decode_error_count", "audio", "decode_error_count", None,
                source="ffmpeg:decode", notes="not performed: ffmpeg is not available",
            )
        )
    return measurements


def _gather_subtitle_measurements(
    subtitle_path: Path, video_duration_sec: Optional[float], params: Dict[str, Any]
) -> List[QCMeasurement]:
    return measure_subtitle(
        subtitle_path,
        video_duration_sec=video_duration_sec,
        max_line_length=params["max_line_length"],
        max_cue_duration=params["max_cue_duration_sec"],
        max_gap_sec=params["max_gap_sec"],
    )


def _build_checks_and_findings(
    request: Request, measurements: List[QCMeasurement], video_duration_sec: Optional[float]
):
    mmap = _measurement_map(measurements)
    checks: List[QCCheck] = []
    findings: List[QCFinding] = []

    if request.kind == "video":
        c, f = evaluate_video(mmap, request.video_rule, video_duration_sec)
        checks += c
        findings += f
    elif request.kind == "audio":
        c, f = evaluate_audio(mmap, request.audio_rule)
        checks += c
        findings += f
    elif request.kind == "subtitle":
        c, f = evaluate_subtitle(mmap, request.subtitle_rule)
        checks += c
        findings += f
    elif request.kind == "delivery":
        rule = request.delivery_rule
        if rule is not None:
            c, f = evaluate_delivery_basics(mmap, rule)
            checks += c
            findings += f
            if rule.require_video is not None or rule.video is not None:
                require_stream = rule.require_video if rule.require_video is not None else True
                c2, f2 = evaluate_video(mmap, rule.video, video_duration_sec, require_stream=require_stream)
                checks += c2
                findings += f2
            if rule.require_audio is not None or rule.audio is not None:
                audio_rule = _with_requirement(rule.audio, AudioRule, "require_audio_stream", rule.require_audio)
                c3, f3 = evaluate_audio(mmap, audio_rule)
                checks += c3
                findings += f3
            if rule.require_subtitle is not None or rule.subtitle is not None:
                subtitle_rule = _with_requirement(rule.subtitle, SubtitleRule, "require_subtitle", rule.require_subtitle)
                c4, f4 = evaluate_subtitle(mmap, subtitle_rule)
                checks += c4
                findings += f4
    return checks, findings


def _with_requirement(sub_rule, cls, field_name: str, delivery_level_value: Optional[bool]):
    """Fold a delivery-level require_video/require_audio/require_subtitle
    flag into the corresponding sub-rule's own requirement field, unless
    the sub-rule already specifies one explicitly (the sub-rule wins).
    """

    if sub_rule is not None and getattr(sub_rule, field_name) is not None:
        return sub_rule
    if sub_rule is None:
        return cls(**{field_name: delivery_level_value}) if delivery_level_value is not None else cls()
    from dataclasses import replace

    return replace(sub_rule, **{field_name: delivery_level_value})


def _effective_parameters(request: Request) -> Dict[str, Any]:
    return {**DEFAULT_PARAMETERS, **request.parameters}


def _identity(
    *,
    asset_fingerprints: List[str],
    kind: str,
    operation: str,
    effective_parameters: Dict[str, Any],
    rules_payload: Dict[str, Any],
    caps: CapabilitySet,
) -> str:
    payload = {
        "skill": SKILL_ID,
        "skill_version": SKILL_VERSION,
        "kind": kind,
        "operation": operation,
        "asset_fingerprints": asset_fingerprints,
        "effective_parameters": effective_parameters,
        "rules": rules_payload,
        "ffmpeg_version": caps.ffmpeg_version,
        "ffprobe_version": caps.ffprobe_version,
    }
    return stable_hash(payload)


def _rules_payload(request: Request) -> Dict[str, Any]:
    def as_dict(rule):
        if rule is None:
            return None
        from dataclasses import asdict

        return asdict(rule)

    return {
        "video": as_dict(request.video_rule),
        "audio": as_dict(request.audio_rule),
        "subtitle": as_dict(request.subtitle_rule),
        "delivery": as_dict(request.delivery_rule),
    }


def run_report(request: Request, ctx: ExecutionContext) -> Dict[str, Any]:
    """Build a QCReport for one request. Returns the full response payload
    (STEP 18): {status, skill, skill_version, report, provenance, reused, cache}.
    """

    if not ctx.capabilities.ffprobe_available:
        raise error("DEPENDENCY_ERROR", "ffprobe is required but was not found on PATH")

    input_path = ctx.path_policy.resolve_input(request.input)
    fingerprint = sha256_file(input_path)
    size_bytes = input_path.stat().st_size

    subtitle_path = None
    subtitle_fingerprint = None
    if request.kind == "delivery" and request.subtitle:
        subtitle_path = ctx.path_policy.resolve_input(request.subtitle)
        subtitle_fingerprint = sha256_file(subtitle_path)
    reference_video_path = None
    reference_fingerprint = None
    if request.kind == "subtitle" and request.reference_video:
        reference_video_path = ctx.path_policy.resolve_input(request.reference_video)
        reference_fingerprint = sha256_file(reference_video_path)

    effective_parameters = _effective_parameters(request)
    rules_payload = _rules_payload(request)
    asset_fingerprints = [fingerprint] + ([subtitle_fingerprint] if subtitle_fingerprint else []) + (
        [reference_fingerprint] if reference_fingerprint else []
    )

    identity = _identity(
        asset_fingerprints=asset_fingerprints,
        kind=request.kind,
        operation=request.operation,
        effective_parameters=effective_parameters,
        rules_payload=rules_payload,
        caps=ctx.capabilities,
    )
    cache_key = identity
    cache_metadata = {
        "skill_version": SKILL_VERSION,
        "asset_fingerprints": asset_fingerprints,
        "kind": request.kind,
        "operation": request.operation,
        "effective_parameters": effective_parameters,
        "ffmpeg_version": ctx.capabilities.ffmpeg_version,
        "ffprobe_version": ctx.capabilities.ffprobe_version,
    }

    reused = False
    cache_status = "disabled" if ctx.cache is None else "miss"

    if ctx.cache is not None and request.cache_policy in ("use", "only"):
        cached = ctx.cache.get(cache_key, cache_metadata)
        if cached is not None:
            reused = True
            cache_status = "hit"
            return {
                "schema": "qc/response@1",
                "status": "completed",
                "skill": {"id": SKILL_ID, "version": SKILL_VERSION},
                "report": cached,
                "provenance": cached.get("provenance", {}),
                "reused": True,
                "cache": {"status": cache_status, "policy": request.cache_policy, "key": cache_key},
            }
        cache_status = "miss"

    if request.cache_policy == "only":
        raise error("VALIDATION_ERROR", "cache_policy is 'only' but no cached report was found", key=cache_key)

    video_duration_sec = None

    if request.kind == "video":
        measurements = _gather_video_measurements(ctx, input_path, effective_parameters)
        duration_m = next((m for m in measurements if m.id == "container.duration_sec"), None)
        video_duration_sec = duration_m.value if duration_m else None
    elif request.kind == "audio":
        measurements = _gather_audio_measurements(ctx, input_path, effective_parameters)
    elif request.kind == "subtitle":
        ref_duration = None
        if reference_video_path is not None:
            ref_probe = probe_media(ctx.capabilities.ffprobe_path, reference_video_path)
            fmt_duration = ref_probe.get("format", {}).get("duration")
            ref_duration = float(fmt_duration) if fmt_duration is not None else None
        measurements = _gather_subtitle_measurements(input_path, ref_duration, effective_parameters)
    elif request.kind == "delivery":
        measurements = []
        fmt_probe = probe_media(ctx.capabilities.ffprobe_path, input_path)
        measurements += measure_container(fmt_probe, actual_size_bytes=size_bytes)
        measurements.append(QCMeasurement("delivery.extension", "delivery", "extension", input_path.suffix.lower().lstrip("."), source="OBSERVED"))
        v = video_streams(fmt_probe)
        a = audio_streams(fmt_probe)
        if v:
            measurements += _gather_video_measurements(ctx, input_path, effective_parameters)
        else:
            measurements += measure_video_streams(fmt_probe)
        if a:
            measurements += _gather_audio_measurements(ctx, input_path, effective_parameters)
        else:
            measurements += measure_audio_streams(fmt_probe)
        measurements = _dedupe_measurements(measurements)
        duration_m = next((m for m in measurements if m.id == "container.duration_sec"), None)
        video_duration_sec = duration_m.value if duration_m else None
        if subtitle_path is not None:
            sub_measurements = _gather_subtitle_measurements(subtitle_path, video_duration_sec, effective_parameters)
            measurements += sub_measurements
        else:
            measurements.append(QCMeasurement("subtitle.exists", "subtitle", "exists", False, source="OBSERVED"))
    else:
        raise error("INVALID_REQUEST", f"unsupported kind: {request.kind}")

    measurements = _dedupe_measurements(measurements)

    checks: List[QCCheck] = []
    findings: List[QCFinding] = []
    if request.operation in ("check", "validate"):
        checks, findings = _build_checks_and_findings(request, measurements, video_duration_sec)

    observed_at = now_iso(ctx.clock)
    provenance = {
        "skill": SKILL_ID,
        "skill_version": SKILL_VERSION,
        "operation": request.operation,
        "engine": {
            "ffmpeg_version": ctx.capabilities.ffmpeg_version or None,
            "ffprobe_version": ctx.capabilities.ffprobe_version or None,
        },
        "input": {"fingerprint": fingerprint, "size_bytes": size_bytes},
        "identity": identity,
        "observed_at": observed_at,
        "measurement_source": "OBSERVED",
    }
    if subtitle_fingerprint:
        provenance["subtitle_input"] = {"fingerprint": subtitle_fingerprint}
    if reference_fingerprint:
        provenance["reference_video_input"] = {"fingerprint": reference_fingerprint}

    report = QCReport(
        id=f"qcreport_{identity[:16]}",
        version="1",
        operation=request.operation,
        kind=request.kind,
        input={"kind": request.kind, "fingerprint": fingerprint, "size_bytes": size_bytes},
        checks=checks,
        measurements=measurements,
        findings=findings,
        provenance=provenance,
    )
    report_dict = report.to_dict()

    if ctx.cache is not None and request.cache_policy in ("use",):
        ctx.cache.set(cache_key, cache_metadata, report_dict)

    return {
        "schema": "qc/response@1",
        "status": "completed",
        "skill": {"id": SKILL_ID, "version": SKILL_VERSION},
        "report": report_dict,
        "provenance": provenance,
        "reused": reused,
        "cache": {"status": cache_status, "policy": request.cache_policy, "key": cache_key},
    }
