"""contract --json and doctor --json (STEP 11/20 of the task spec).

The contract only ever advertises checks/formats/operations that are
actually implemented; nothing here is aspirational ("future work" belongs
in docs, never in the machine-readable contract or doctor output).
"""

from __future__ import annotations

import json
import os
import platform
import sys
from typing import Any, Dict

from . import CONTRACT_VERSION, PACKAGE_NAME, SKILL_ID, VERSION
from .capabilities import REQUIRED_FILTERS, detect_capabilities
from .errors import ERROR_CODES

SUPPORTED_OPERATIONS = ["inspect", "check", "validate"]
SUPPORTED_KINDS = ["video", "audio", "subtitle", "delivery"]

SUPPORTED_VIDEO_MEASUREMENTS = [
    "container.format_name", "container.duration_sec", "container.size_bytes", "container.bit_rate",
    "video.stream_present", "video.stream_count", "video.codec", "video.width", "video.height",
    "video.aspect_ratio", "video.frame_rate", "video.frame_count", "video.pixel_format",
    "video.color_range", "video.color_space", "video.color_transfer", "video.color_primaries", "video.field_order",
    "video.black_segments", "video.freeze_segments", "video.decoded_frame_count",
    "video.decode_error_count", "video.decode_errors", "video.frame_count_delta",
]

SUPPORTED_AUDIO_MEASUREMENTS = [
    "audio.stream_present", "audio.stream_count", "audio.codec", "audio.sample_rate",
    "audio.channels", "audio.channel_layout", "audio.duration_sec",
    "audio.peak_level_dbfs", "audio.rms_level_dbfs", "audio.clipping_detected", "audio.per_channel_levels",
    "audio.integrated_loudness_lufs", "audio.loudness_range_lu", "audio.true_peak_dbfs",
    "audio.silence_segments", "audio.leading_silence_sec", "audio.trailing_silence_sec",
    "audio.internal_silence_segments", "audio.decode_error_count", "audio.decode_errors",
]

SUPPORTED_SUBTITLE_MEASUREMENTS = [
    "subtitle.exists", "subtitle.format", "subtitle.cue_count", "subtitle.invalid_timestamps",
    "subtitle.overlapping_cues", "subtitle.empty_cues", "subtitle.duplicate_ids",
    "subtitle.invalid_control_characters", "subtitle.duration_sec", "subtitle.coverage_ratio",
    "subtitle.duration_delta_sec", "subtitle.cue_density_per_min", "subtitle.excessive_line_length",
    "subtitle.excessive_cue_duration", "subtitle.gaps",
]

SUPPORTED_DELIVERY_MEASUREMENTS = ["delivery.extension"] + SUPPORTED_VIDEO_MEASUREMENTS + SUPPORTED_AUDIO_MEASUREMENTS + SUPPORTED_SUBTITLE_MEASUREMENTS

SUPPORTED_CHECKS = [
    "video.stream_present", "video.decodes_without_errors", "video.resolution_matches_expected",
    "video.frame_rate_matches_expected", "video.codec_matches_expected", "video.pixel_format_matches_expected",
    "video.aspect_ratio_matches_expected", "video.black_frames_within_tolerance", "video.freeze_frames_within_tolerance",
    "audio.stream_present_matches_expected", "audio.decodes_without_errors", "audio.no_clipping",
    "audio.sample_rate_matches_expected", "audio.channels_match_expected", "audio.channel_layout_matches_expected",
    "audio.leading_silence_within_tolerance", "audio.trailing_silence_within_tolerance",
    "audio.internal_silence_within_tolerance", "audio.integrated_loudness_within_tolerance",
    "audio.true_peak_within_tolerance", "audio.loudness_range_within_tolerance", "audio.channel_balance_within_tolerance",
    "subtitle.presence_matches_expected", "subtitle.timestamps_valid", "subtitle.no_empty_cues",
    "subtitle.no_duplicate_ids", "subtitle.no_control_characters", "subtitle.no_overlapping_cues",
    "subtitle.line_length_within_limit", "subtitle.cue_duration_within_limit", "subtitle.gaps_within_limit",
    "subtitle.duration_matches_video", "subtitle.coverage_within_limit",
    "delivery.file_size_within_limit", "delivery.extension_matches_expected", "delivery.container_matches_expected",
]

SUPPORTED_FORMATS = {
    "video_containers": ["any container ffprobe/ffmpeg on this host can demux (detected by doctor, not assumed)"],
    "subtitle_formats": ["srt", "vtt", "ass", "ssa"],
}

NOT_PROVIDED = [
    "production decisions (publish/re-render/block)",
    "automatic editing or repair",
    "automatic re-render",
    "AI/LLM-based judgment of measured facts",
    "transcription, diarization, speaker recognition",
    "semantic or scene understanding",
    "subtitle generation, rewriting, or translation",
    "arbitrary ffmpeg filter graphs or arbitrary shell execution",
]


def tool_spec(check_id: str, category: str) -> Dict[str, Any]:
    return {"tool_id": f"{SKILL_ID}/{check_id}", "skill_id": SKILL_ID, "version": VERSION, "category": category}


def skill_contract() -> Dict[str, Any]:
    return {
        "schema": f"{SKILL_ID}/contract@{CONTRACT_VERSION}",
        "skill_id": SKILL_ID,
        "name": SKILL_ID,
        "package": PACKAGE_NAME,
        "version": VERSION,
        "contract_version": CONTRACT_VERSION,
        "description": (
            "Deterministic media quality control / validation. Measures and checks "
            "video, audio, subtitle and delivery artifacts against caller-supplied "
            "rules; never decides whether media may ship."
        ),
        "role": "observation / validation",
        "not_provided": NOT_PROVIDED,
        "operations": SUPPORTED_OPERATIONS,
        "kinds": SUPPORTED_KINDS,
        "capabilities": {
            "required": ["ffprobe"],
            "optional": ["ffmpeg", "filter:blackdetect", "filter:freezedetect", "filter:ebur128", "filter:astats", "filter:silencedetect"],
        },
        "inputs": {
            "input": "media or subtitle file path (one primary artifact per request)",
            "subtitle": "companion subtitle file path (kind=delivery only)",
            "reference_video": "companion video file path, for duration comparison (kind=subtitle only)",
        },
        "outputs": ["report"],
        "parameters": sorted(
            {
                "black_min_duration_sec", "black_pixel_threshold", "freeze_noise_db", "freeze_min_duration_sec",
                "silence_threshold_db", "silence_min_duration_sec", "clipping_threshold_dbfs",
                "max_line_length", "max_cue_duration_sec", "max_gap_sec",
            }
        ),
        "measurements": {
            "video": SUPPORTED_VIDEO_MEASUREMENTS,
            "audio": SUPPORTED_AUDIO_MEASUREMENTS,
            "subtitle": SUPPORTED_SUBTITLE_MEASUREMENTS,
            "delivery": SUPPORTED_DELIVERY_MEASUREMENTS,
        },
        "checks": SUPPORTED_CHECKS,
        "formats": SUPPORTED_FORMATS,
        "statuses": ["PASS", "WARN", "FAIL", "UNKNOWN"],
        "execution": {
            "mode": "local_subprocess",
            "canonical_invocation": ["qc", "run", "-", "--json"],
            "stdin": "request document JSON when the run argument is '-'",
            "stdout": "exactly one response document when --json is given",
            "stderr": "diagnostics only; never part of the contract",
            "shell": False,
            "arbitrary_executables": False,
            "arbitrary_filters": False,
            "network": False,
            "input_mutation": False,
            "executables": ["ffprobe", "ffmpeg"],
            "executable_resolution": "PATH lookup only; not configurable through the request",
        },
        "deterministic": True,
        "identity": {
            "components": ["asset_fingerprint(sha256 of content)", "kind", "operation", "effective_parameters", "rules", "ffmpeg_version", "ffprobe_version"],
            "excludes": ["timestamps", "machine-local paths", "request_id"],
            "canonical_json": True,
        },
        "cache": {"policies": ["use", "bypass", "only"], "statuses": ["hit", "miss", "invalid", "disabled"]},
        "provenance": "OBSERVED",
        "errors": {
            "codes": sorted(ERROR_CODES),
            "exit_codes": {code: v[0] for code, v in ERROR_CODES.items()},
            "success_exit_code": 0,
        },
    }


def _python_info() -> Dict[str, Any]:
    return {"version": sys.version.split()[0], "implementation": platform.python_implementation(), "platform": platform.platform()}


def doctor_report(path_policy_workspace: str) -> Dict[str, Any]:
    caps = detect_capabilities()

    checks: Dict[str, Any] = {"python": {"status": "AVAILABLE", **_python_info()}}
    checks["path_policy"] = {
        "status": "AVAILABLE" if os.path.isdir(path_policy_workspace) else "MISSING",
        "workspace": path_policy_workspace,
        "workspace_exists": os.path.isdir(path_policy_workspace),
    }

    checks["ffprobe"] = (
        {"status": "AVAILABLE", "version": caps.ffprobe_version, "path": caps.ffprobe_path}
        if caps.ffprobe_available
        else {"status": "MISSING", "detail": "ffprobe was not found on PATH"}
    )
    checks["ffmpeg"] = (
        {"status": "AVAILABLE", "version": caps.ffmpeg_version, "path": caps.ffmpeg_path}
        if caps.ffmpeg_available
        else {"status": "MISSING", "detail": "ffmpeg was not found on PATH; video/audio defect analysis is degraded to metadata-only"}
    )

    for name in REQUIRED_FILTERS:
        state = caps.filters.get(name, "unknown")
        status = {"available": "AVAILABLE", "missing": "MISSING", "unknown": "UNKNOWN"}[state]
        checks[f"filter:{name}"] = {"status": status}

    try:
        contract = skill_contract()
        json.dumps(contract)  # round-trip: every value must be JSON-serializable
        checks["contract"] = {"status": "AVAILABLE", "schema": contract["schema"], "checks": len(contract["checks"])}
        contract_ok = True
    except Exception as exc:  # pragma: no cover - defensive
        checks["contract"] = {"status": "MISSING", "detail": str(exc)}
        contract_ok = False

    unavailable_tools = []
    if not caps.ffprobe_available:
        unavailable_tools.append("inspect")
        unavailable_tools.append("check")
    if not caps.ffmpeg_available:
        unavailable_tools.append("video.black_frames_within_tolerance")
        unavailable_tools.append("video.freeze_frames_within_tolerance")
        unavailable_tools.append("audio.no_clipping")

    problems = []
    if not caps.ffprobe_available:
        problems.append("ffprobe is required and was not found on PATH")
    if not contract_ok:
        problems.append("contract is not self-consistent")

    if not caps.ffprobe_available or not contract_ok:
        status = "fail"
    elif not caps.ffmpeg_available or any(v == "unknown" for v in caps.filters.values()):
        status = "degraded"
    else:
        status = "ok"

    return {
        "schema": f"{SKILL_ID}/doctor@1",
        "skill": {"id": SKILL_ID, "version": VERSION},
        "status": status,
        "checks": checks,
        "unavailable_tools": sorted(set(unavailable_tools)),
        "problems": problems,
    }
