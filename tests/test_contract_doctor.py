import json

from qc_skill import CONTRACT_VERSION, SKILL_ID, VERSION
from qc_skill.contract import doctor_report, skill_contract
from qc_skill.errors import ERROR_CODES


def test_contract_is_json_serializable():
    doc = skill_contract()
    json.dumps(doc)  # must not raise


def test_contract_identifies_the_skill():
    doc = skill_contract()
    assert doc["skill_id"] == SKILL_ID == "qc"
    assert doc["version"] == VERSION
    assert doc["contract_version"] == CONTRACT_VERSION
    assert doc["schema"] == f"qc/contract@{CONTRACT_VERSION}"


def test_contract_lists_only_implemented_operations():
    doc = skill_contract()
    assert set(doc["operations"]) == {"inspect", "check", "validate"}
    assert set(doc["kinds"]) == {"video", "audio", "subtitle", "delivery"}


def test_contract_never_advertises_unimplemented_features():
    doc = skill_contract()
    # These belong only in not_provided (an explicit disclaimer) - never as
    # an advertised operation, check, or measurement.
    banned = ("transcription", "diarization", "translation", "auto-repair", "autorepair", "llm", "thumbnail")
    for key in ("operations", "kinds", "checks"):
        haystack = json.dumps(doc[key]).lower()
        for term in banned:
            assert term not in haystack, f"{term!r} must not appear in contract[{key!r}]"
    for kind_measurements in doc["measurements"].values():
        haystack = json.dumps(kind_measurements).lower()
        for term in banned:
            assert term not in haystack, f"{term!r} must not appear in contract measurements"


def test_contract_declares_security_boundary():
    doc = skill_contract()
    execu = doc["execution"]
    assert execu["shell"] is False
    assert execu["arbitrary_executables"] is False
    assert execu["arbitrary_filters"] is False
    assert execu["network"] is False


def test_contract_error_codes_match_errors_module():
    doc = skill_contract()
    assert set(doc["errors"]["codes"]) == set(ERROR_CODES)


def test_contract_statuses_are_the_four_qc_statuses():
    doc = skill_contract()
    assert set(doc["statuses"]) == {"PASS", "WARN", "FAIL", "UNKNOWN"}


def test_contract_deterministic_flag_is_true():
    assert skill_contract()["deterministic"] is True


def test_doctor_reports_ok_when_ffmpeg_and_ffprobe_present(tmp_path):
    report = doctor_report(str(tmp_path))
    assert report["schema"] == "qc/doctor@1"
    assert report["skill"]["id"] == "qc"
    assert report["status"] in ("ok", "degraded")
    assert report["checks"]["ffprobe"]["status"] == "AVAILABLE"
    assert report["checks"]["contract"]["status"] == "AVAILABLE"


def test_doctor_never_reports_available_for_a_missing_workspace():
    report = doctor_report("/definitely/does/not/exist/qc-workspace")
    assert report["checks"]["path_policy"]["status"] == "MISSING"


def test_doctor_reports_all_required_filters():
    report = doctor_report(".")
    for name in ("blackdetect", "freezedetect", "ebur128", "astats", "silencedetect"):
        assert f"filter:{name}" in report["checks"]
        assert report["checks"][f"filter:{name}"]["status"] in ("AVAILABLE", "MISSING", "UNKNOWN")
