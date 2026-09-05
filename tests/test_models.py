from qc_skill.models import FindingSeverity, QCCheck, QCFinding, QCMeasurement, QCReport, QCStatus


def test_status_aggregate_empty_is_unknown():
    assert QCStatus.aggregate([]) == QCStatus.UNKNOWN


def test_status_aggregate_fail_dominates():
    assert QCStatus.aggregate([QCStatus.PASS, QCStatus.WARN, QCStatus.FAIL, QCStatus.UNKNOWN]) == QCStatus.FAIL


def test_status_aggregate_unknown_beats_warn():
    assert QCStatus.aggregate([QCStatus.PASS, QCStatus.WARN, QCStatus.UNKNOWN]) == QCStatus.UNKNOWN


def test_status_aggregate_all_pass():
    assert QCStatus.aggregate([QCStatus.PASS, QCStatus.PASS]) == QCStatus.PASS


def test_report_overall_status_no_checks_is_unknown():
    report = QCReport(id="r1", version="1", operation="inspect", kind="video", input={})
    assert report.overall_status == QCStatus.UNKNOWN


def test_report_overall_status_reflects_worst_check():
    report = QCReport(
        id="r1", version="1", operation="check", kind="video", input={},
        checks=[
            QCCheck("a", "video", QCStatus.PASS),
            QCCheck("b", "video", QCStatus.WARN),
        ],
    )
    assert report.overall_status == QCStatus.WARN


def test_measurement_to_dict_omits_none_optional_fields():
    m = QCMeasurement(id="video.width", category="video", name="width", value=1920)
    d = m.to_dict()
    assert "unit" not in d
    assert "stream" not in d
    assert d["value"] == 1920
    assert d["estimated"] is False


def test_measurement_to_dict_includes_set_optional_fields():
    m = QCMeasurement(id="video.width", category="video", name="width", value=1920, unit="px", stream=0, notes="n")
    d = m.to_dict()
    assert d["unit"] == "px"
    assert d["stream"] == 0
    assert d["notes"] == "n"


def test_finding_to_dict_shape():
    f = QCFinding(code="X", severity=FindingSeverity.FAIL, message="boom", evidence={"a": 1})
    d = f.to_dict()
    assert d["code"] == "X"
    assert d["severity"] == "FAIL"
    assert d["evidence"] == {"a": 1}


def test_report_to_dict_is_json_shaped():
    report = QCReport(
        id="r1", version="1", operation="check", kind="audio", input={"path": "x"},
        checks=[QCCheck("c1", "audio", QCStatus.FAIL, finding_codes=["F1"])],
        measurements=[QCMeasurement("m1", "audio", "m1", 1)],
        findings=[QCFinding("F1", FindingSeverity.FAIL, "bad")],
    )
    d = report.to_dict()
    assert d["overall_status"] == "FAIL"
    assert len(d["checks"]) == 1
    assert len(d["measurements"]) == 1
    assert len(d["findings"]) == 1
