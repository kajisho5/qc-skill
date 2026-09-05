"""Contract completeness: `qc contract --json` must neither advertise a
check/measurement that doesn't exist, nor omit one that does (STEP 20 of
the task spec: doctor/contract must reflect only - and all of - what is
actually implemented).

These tests are deliberately independent of any manually-maintained
mapping: they parse the actual source of rules.py and measurements/*.py
for every id string passed to ``QCCheck``/``QCMeasurement``, and diff that
against ``contract.py``'s declared lists.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from qc_skill import contract as contract_module
from qc_skill import rules as rules_module
from qc_skill.measurements import audio as audio_module
from qc_skill.measurements import subtitle as subtitle_module
from qc_skill.measurements import video as video_module
from qc_skill import engine as engine_module

_CHECK_ID_RE = re.compile(r'QCCheck\(\s*"([a-z][a-z0-9_.]*)"')
_UNKNOWN_CHECK_ID_RE = re.compile(r'_unknown_check\(\s*"([a-z][a-z0-9_.]*)"')
_EQUALITY_CHECK_ID_RE = re.compile(r'check_id="([a-z][a-z0-9_.]*)"')
_FINDING_CODE_DIRECT_RE = re.compile(r'QCFinding\(\s*"([A-Z][A-Z0-9_]*)"')
_FINDING_CODE_TERNARY_RE = re.compile(r'"([A-Z][A-Z0-9_]*)"\s+if\s+.+\s+else\s+"([A-Z][A-Z0-9_]*)"')
_FINDING_CODE_KW_RE = re.compile(r'code="([A-Z][A-Z0-9_]*)"')
_MEASUREMENT_ID_LITERAL_RE = re.compile(r'QCMeasurement\(\s*"([a-z][a-z0-9_.]*)"')
_MEASUREMENT_ID_KW_RE = re.compile(r'QCMeasurement\(\s*id="([a-z][a-z0-9_.]*)"')

# Built via an f-string in measurements/video.py (per-field color/field_order
# metadata) rather than a literal, so it cannot be picked up by the regexes
# above; every id it can produce is enumerated once, here, and cross-checked
# against the source below so this list itself cannot silently go stale.
_DYNAMIC_VIDEO_FIELD_MEASUREMENTS = {
    "video.color_range", "video.color_space", "video.color_transfer",
    "video.color_primaries", "video.field_order",
}


def _source_of(module) -> str:
    return inspect.getsource(module)


def _actual_check_ids() -> set:
    src = _source_of(rules_module)
    return (
        set(_CHECK_ID_RE.findall(src))
        | set(_UNKNOWN_CHECK_ID_RE.findall(src))
        | set(_EQUALITY_CHECK_ID_RE.findall(src))
    )


def _actual_measurement_ids() -> set:
    ids = set()
    for module in (video_module, audio_module, subtitle_module, engine_module):
        src = _source_of(module)
        ids |= set(_MEASUREMENT_ID_LITERAL_RE.findall(src))
        ids |= set(_MEASUREMENT_ID_KW_RE.findall(src))
    return ids | _DYNAMIC_VIDEO_FIELD_MEASUREMENTS


def test_dynamic_video_field_measurement_ids_are_still_produced_by_the_source():
    src = _source_of(video_module)
    assert 'f"video.{field_name}"' in src, (
        "the hand-maintained _DYNAMIC_VIDEO_FIELD_MEASUREMENTS allowlist in this test "
        "assumes measurements/video.py still builds these ids via this f-string; "
        "if that changed, update both the source check and the allowlist together"
    )
    assert 'for field_name in ("color_range", "color_space", "color_transfer", "color_primaries", "field_order")' in src


def test_every_implemented_check_is_in_the_contract():
    actual = _actual_check_ids()
    declared = set(contract_module.SUPPORTED_CHECKS)
    missing_from_contract = actual - declared
    assert not missing_from_contract, f"implemented but not advertised in contract.SUPPORTED_CHECKS: {sorted(missing_from_contract)}"


def test_contract_never_advertises_a_check_that_does_not_exist():
    actual = _actual_check_ids()
    declared = set(contract_module.SUPPORTED_CHECKS)
    phantom = declared - actual
    assert not phantom, f"advertised in contract.SUPPORTED_CHECKS but not produced by rules.py: {sorted(phantom)}"


def test_every_implemented_measurement_is_in_the_contract():
    actual = _actual_measurement_ids()
    declared = (
        set(contract_module.SUPPORTED_VIDEO_MEASUREMENTS)
        | set(contract_module.SUPPORTED_AUDIO_MEASUREMENTS)
        | set(contract_module.SUPPORTED_SUBTITLE_MEASUREMENTS)
        | set(contract_module.SUPPORTED_DELIVERY_MEASUREMENTS)
    )
    missing_from_contract = actual - declared
    assert not missing_from_contract, f"implemented but not advertised in any contract measurement list: {sorted(missing_from_contract)}"


def test_contract_never_advertises_a_measurement_that_does_not_exist():
    actual = _actual_measurement_ids()
    declared = (
        set(contract_module.SUPPORTED_VIDEO_MEASUREMENTS)
        | set(contract_module.SUPPORTED_AUDIO_MEASUREMENTS)
        | set(contract_module.SUPPORTED_SUBTITLE_MEASUREMENTS)
        | set(contract_module.SUPPORTED_DELIVERY_MEASUREMENTS)
    )
    phantom = declared - actual
    assert not phantom, f"advertised but never produced by any measurements module: {sorted(phantom)}"


def _actual_finding_codes() -> set:
    src = _source_of(rules_module)
    codes = set(_FINDING_CODE_DIRECT_RE.findall(src)) | set(_FINDING_CODE_KW_RE.findall(src))
    for a, b in _FINDING_CODE_TERNARY_RE.findall(src):
        codes.add(a)
        codes.add(b)
    return codes


def test_every_implemented_finding_code_is_in_the_contract_catalog():
    actual = _actual_finding_codes()
    declared = {code for code, _severity in contract_module.FINDING_CATALOG}
    missing = actual - declared
    assert not missing, f"finding codes emitted by rules.py but missing from contract.FINDING_CATALOG: {sorted(missing)}"


def test_contract_finding_catalog_never_lists_a_code_that_does_not_exist():
    actual = _actual_finding_codes()
    declared = {code for code, _severity in contract_module.FINDING_CATALOG}
    phantom = declared - actual
    assert not phantom, f"listed in contract.FINDING_CATALOG but never emitted by rules.py: {sorted(phantom)}"


def test_finding_catalog_has_no_duplicate_codes():
    codes = [code for code, _severity in contract_module.FINDING_CATALOG]
    assert len(codes) == len(set(codes))


def test_rules_contract_schema_matches_the_actual_dataclass_fields():
    from dataclasses import fields as dc_fields

    schema = contract_module.rules_contract_schema()
    for key, cls in (
        ("video", rules_module.VideoRule),
        ("audio", rules_module.AudioRule),
        ("subtitle", rules_module.SubtitleRule),
        ("delivery", rules_module.DeliveryRule),
    ):
        assert set(schema[key].keys()) == {f.name for f in dc_fields(cls)}


def test_docs_checks_reference_lists_every_check_id():
    docs_path = Path(__file__).resolve().parents[1] / "docs" / "checks.md"
    docs_text = docs_path.read_text(encoding="utf-8")
    missing_from_docs = [c for c in contract_module.SUPPORTED_CHECKS if c not in docs_text]
    assert not missing_from_docs, f"check ids missing from docs/checks.md: {missing_from_docs}"
