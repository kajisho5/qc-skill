"""ADR-023: cue-text limits and subtitle-vs-speech checks.

Most tests parse a real SRT and a real transcript JSON written to
``tmp_path`` and evaluate the rule on the resulting measurements; the
end-to-end tests at the bottom go through ``run`` (request parsing, path
policy, identity) like every other kind.
"""

from __future__ import annotations

import json
import shutil

import pytest

from qc_skill.errors import QCError
from qc_skill.measurements.subtitle import measure_subtitle
from qc_skill.measurements.transcript import measure_transcript, read_transcript
from qc_skill.models import QCStatus
from qc_skill.rules import SUBTITLE_PRESETS, CoveredInterval, SubtitleRule, evaluate_subtitle, resolve_subtitle_preset
from qc_skill.schemas import parse_request
from tests.helpers import checks_by_id, measurements_by_id, run


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(path, cues):
    blocks = []
    for i, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{i}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _transcript_doc(words, *, key="text", with_index=False):
    entries = []
    for i, w in enumerate(words):
        text, start, end = w[0], w[1], w[2]
        extra = w[3] if len(w) > 3 else {}
        entry = {key: text, "start": start, "end": end, **extra}
        if with_index:
            entry["index"] = i
        entries.append(entry)
    return {"segments": [{"start": 0.0, "end": 99.0, "words": entries}]}


def _evaluate(tmp_path, cues, words=None, *, transcript_doc=None, **rule_kwargs):
    srt = _write_srt(tmp_path / "subs.srt", cues)
    measurements = measure_subtitle(srt, include_cue_texts=True)
    if words is not None or transcript_doc is not None:
        doc = transcript_doc if transcript_doc is not None else _transcript_doc(words)
        tpath = tmp_path / "transcript.json"
        tpath.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        measurements += measure_transcript(tpath)
    mmap = {m.id: m for m in measurements}
    checks, findings = evaluate_subtitle(mmap, SubtitleRule(**rule_kwargs))
    return {c.check_id: c for c in checks}, findings


HELLO_WORLD = [("Hello", 1.0, 1.4), ("world", 1.5, 1.9), ("how", 2.1, 2.3), ("are", 2.3, 2.5), ("you", 2.5, 2.9)]


# --- tolerant transcript reading -------------------------------------------


def test_reads_text_or_word_and_derives_position_without_index(tmp_path):
    doc = {"segments": [
        {"words": [{"text": " Hello ", "start": 0.0, "end": 0.4}, {"word": "world", "start": 0.5, "end": 0.9}]},
        {"words": [{"word": "again", "start": 1.0, "end": 1.3, "pos": "Noun"}]},
    ]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    result = read_transcript(path)
    assert result.error is None
    assert [w["text"] for w in result.words] == ["Hello", "world", "again"]
    assert [w["position"] for w in result.words] == [0, 1, 2]
    assert [w["index"] for w in result.words] == [None, None, None]
    assert [w["pos"] for w in result.words] == [None, None, "noun"]


def test_keeps_the_transcripts_own_index_and_interpolation_flags(tmp_path):
    doc = {"words": [
        {"text": "a", "start": 0.0, "end": 0.1, "index": 7},
        {"text": "b", "start": 0.2, "end": 0.3, "index": 8, "interpolated": True},
        {"text": "c", "start": 0.4, "end": 0.5, "index": 9, "timing_interpolated": True},
    ]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    words = read_transcript(path).words
    assert [w["index"] for w in words] == [7, 8, 9]
    assert [w["interpolated"] for w in words] == [False, True, True]


def test_reads_the_released_transcription_format_with_null_words_and_timing(tmp_path):
    # transcription-skill 0.9.0+: contiguous index, `words: null` segments consume no
    # numbers, `pos` and `timing` only on dictionary words
    doc = {"segments": [
        {"text": "学校に", "words": [
            {"word": "学校", "start": 1.0, "end": 1.4, "index": 0, "pos": "noun", "timing": "measured"},
            {"word": "に", "start": 1.4, "end": 1.5, "index": 1, "pos": "particle", "timing": "interpolated"},
        ]},
        {"text": "(music)", "words": None},
        {"text": "hello", "words": [{"word": "hello", "start": 3.0, "end": 3.4, "index": 2}]},
    ]}
    path = tmp_path / "t.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    words = read_transcript(path).words
    assert [(w["text"], w["index"], w["position"]) for w in words] == [("学校", 0, 0), ("に", 1, 1), ("hello", 2, 2)]
    assert [w["pos"] for w in words] == ["noun", "particle", None]
    assert [w["interpolated"] for w in words] == [False, True, False]


def test_unusable_words_are_skipped_and_counted_and_positions_stay_aligned(tmp_path):
    doc = [{"words": [
        {"text": "ok", "start": 0.0, "end": 0.2},
        {"text": "nostart", "end": 0.4},
        {"text": "bool", "start": True, "end": 0.6},
        {"text": "backwards", "start": 0.9, "end": 0.8},
        {"text": "  ", "start": 1.0, "end": 1.1},
        "not an object",
        {"text": "last", "start": 1.2, "end": 1.4},
    ]}]
    path = tmp_path / "t.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    m = {x.id: x for x in measure_transcript(path)}
    assert [w["text"] for w in m["subtitle.transcript_words"].value] == ["ok", "last"]
    assert [w["position"] for w in m["subtitle.transcript_words"].value] == [0, 6]
    assert m["subtitle.transcript_skipped_word_count"].value == 5
    assert m["subtitle.transcript_pos_word_count"].value == 0


def test_bom_is_accepted(tmp_path):
    path = tmp_path / "t.json"
    path.write_bytes(b"\xef\xbb\xbf" + json.dumps({"words": [{"text": "x", "start": 0, "end": 1}]}).encode())
    assert read_transcript(path).error is None


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps({"text": "no words here"}),
    # segments without word timestamps: zero usable words must not pass vacuously
    json.dumps({"segments": [{"text": "Hello world", "start": 1.0, "end": 2.0}]}),
    json.dumps({"words": [{"text": "Hello", "start": 1.0}]}),
])
def test_unreadable_transcript_makes_speech_checks_unknown(tmp_path, content):
    srt = _write_srt(tmp_path / "subs.srt", [(1.0, 2.0, "Hello world")])
    tpath = tmp_path / "t.json"
    tpath.write_text(content, encoding="utf-8")
    measurements = measure_subtitle(srt, include_cue_texts=True) + measure_transcript(tpath)
    mmap = {m.id: m for m in measurements}
    assert mmap["subtitle.transcript_words"].value is None
    checks, findings = evaluate_subtitle(mmap, SubtitleRule(max_cue_lead_sec=0.3, word_boundary_tolerance_sec=0.0, max_uncovered_speech_sec=1.0))
    by_id = {c.check_id: c for c in checks}
    for check_id in ("subtitle.cue_not_early", "subtitle.cue_boundaries_on_word_boundaries", "subtitle.speech_covered"):
        assert by_id[check_id].status == QCStatus.UNKNOWN
        assert by_id[check_id].reason
    assert findings == []


# --- subtitle.cue_boundaries_on_word_boundaries ------------------------------


def test_word_inside_a_long_overlapping_cue_is_assigned_to_it(tmp_path):
    # a 60 s cue overlapped by ten short ones: a word between two short cues belongs to the long one
    cues = [(0.0, 60.0, "Hello world")] + [(1.0 + i * 2.0, 1.5 + i * 2.0, f"x{i}") for i in range(10)]
    words = [("Hello", 21.6, 21.8), ("world", 21.8, 21.9)]
    checks, _ = _evaluate(tmp_path, cues, words, max_cue_lead_sec=100.0, allow_overlapping_cues=True)
    check = checks["subtitle.cue_not_early"]
    assert check.status == QCStatus.PASS
    assert check.evidence["cues_without_speech"] == 10


def test_boundaries_between_words_pass(tmp_path):
    checks, _ = _evaluate(tmp_path, [(1.0, 2.0, "Hello world"), (2.0, 3.0, "how are you")], HELLO_WORLD, word_boundary_tolerance_sec=0.0)
    check = checks["subtitle.cue_boundaries_on_word_boundaries"]
    assert check.status == QCStatus.PASS
    assert check.evidence["violation_count"] == 0


def test_cue_starting_inside_a_word_fails_with_time_and_cue(tmp_path):
    checks, findings = _evaluate(tmp_path, [(1.2, 2.0, "Hello world")], HELLO_WORLD, word_boundary_tolerance_sec=0.05)
    check = checks["subtitle.cue_boundaries_on_word_boundaries"]
    assert check.status == QCStatus.FAIL
    (violation,) = check.evidence["violations"]
    assert violation["time_sec"] == 1.2
    assert violation["boundary"] == "start"
    assert violation["cue_index"] == 1 and violation["cue_id"] == "1" and violation["text"] == "Hello world"
    assert violation["word"]["text"] == "Hello"
    assert violation["inside_word_by_sec"] == 0.2
    assert "1.200s" in violation["description"]
    (finding,) = findings
    assert finding.code == "SUBTITLE_CUE_BOUNDARY_INSIDE_WORD"
    assert finding.timestamp == 1.2


def test_boundary_tolerance_edge(tmp_path):
    # the cue starts 0.1 s inside "Hello" (1.0-1.4): a tolerance of 0.1 allows it, 0.09 does not
    cues = [(1.1, 2.0, "Hello world")]
    assert _evaluate(tmp_path, cues, HELLO_WORLD, word_boundary_tolerance_sec=0.1)[0]["subtitle.cue_boundaries_on_word_boundaries"].status == QCStatus.PASS
    assert _evaluate(tmp_path, cues, HELLO_WORLD, word_boundary_tolerance_sec=0.09)[0]["subtitle.cue_boundaries_on_word_boundaries"].status == QCStatus.FAIL


def test_boundary_inside_an_interpolated_word_is_unknown_not_fail(tmp_path):
    words = [("Hello", 1.0, 1.4, {"interpolated": True}), ("world", 1.5, 1.9)]
    checks, findings = _evaluate(tmp_path, [(1.2, 2.0, "Hello world")], words, word_boundary_tolerance_sec=0.0)
    check = checks["subtitle.cue_boundaries_on_word_boundaries"]
    assert check.status == QCStatus.UNKNOWN
    assert check.evidence["unconfirmed"][0]["word"]["interpolated"] is True
    assert findings == []


def test_speech_checks_without_a_transcript_are_unknown(tmp_path):
    checks, findings = _evaluate(tmp_path, [(1.0, 2.0, "Hello")], word_boundary_tolerance_sec=0.0, max_cue_lead_sec=0.2, max_uncovered_speech_sec=1.0)
    for check_id in ("subtitle.cue_boundaries_on_word_boundaries", "subtitle.cue_not_early", "subtitle.speech_covered"):
        assert checks[check_id].status == QCStatus.UNKNOWN
        assert "transcript" in checks[check_id].reason
    assert findings == []


# --- subtitle.cue_not_early ----------------------------------------------------


def test_cue_on_time_passes_and_cues_without_speech_are_counted(tmp_path):
    checks, _ = _evaluate(tmp_path, [(0.9, 2.0, "Hello world"), (5.0, 6.0, "(music)")], HELLO_WORLD, max_cue_lead_sec=0.2)
    check = checks["subtitle.cue_not_early"]
    assert check.status == QCStatus.PASS
    assert check.evidence["cues_without_speech"] == 1


def test_early_cue_fails_with_lead_and_first_word(tmp_path):
    checks, findings = _evaluate(tmp_path, [(0.2, 2.0, "Hello world")], HELLO_WORLD, max_cue_lead_sec=0.5)
    check = checks["subtitle.cue_not_early"]
    assert check.status == QCStatus.FAIL
    (violation,) = check.evidence["violations"]
    assert violation["time_sec"] == 0.2
    assert violation["lead_sec"] == 0.8
    assert violation["first_word"]["text"] == "Hello" and violation["first_word"]["start"] == 1.0
    assert findings[0].code == "SUBTITLE_CUE_EARLY"


def test_cue_lead_edge_is_inclusive(tmp_path):
    cues = [(0.5, 2.0, "Hello world")]  # 0.5 s before "Hello"
    assert _evaluate(tmp_path, cues, HELLO_WORLD, max_cue_lead_sec=0.5)[0]["subtitle.cue_not_early"].status == QCStatus.PASS
    assert _evaluate(tmp_path, cues, HELLO_WORLD, max_cue_lead_sec=0.49)[0]["subtitle.cue_not_early"].status == QCStatus.FAIL


# --- subtitle.speech_covered ---------------------------------------------------


def test_speech_fully_under_cues_passes(tmp_path):
    checks, _ = _evaluate(tmp_path, [(1.0, 2.0, "Hello world"), (2.0, 3.0, "how are you")], HELLO_WORLD, max_uncovered_speech_sec=0.0)
    assert checks["subtitle.speech_covered"].status == QCStatus.PASS


def test_uncovered_speech_fails_with_span_text_and_neighbour_cues(tmp_path):
    checks, findings = _evaluate(tmp_path, [(1.0, 2.0, "Hello world")], HELLO_WORLD, max_uncovered_speech_sec=0.5)
    check = checks["subtitle.speech_covered"]
    assert check.status == QCStatus.FAIL
    (violation,) = check.evidence["violations"]
    assert violation["start"] == 2.1 and violation["end"] == 2.9 and violation["duration_sec"] == 0.8
    assert violation["time_sec"] == 2.1
    assert violation["spoken_text"] == "how are you"
    assert violation["previous_cue"]["cue_index"] == 1
    assert violation["next_cue"] is None
    assert findings[0].code == "SUBTITLE_SPEECH_UNCOVERED"


def test_uncovered_speech_edge_and_extra_covered_interval(tmp_path):
    cues = [(1.0, 2.0, "Hello world")]
    assert _evaluate(tmp_path, cues, HELLO_WORLD, max_uncovered_speech_sec=0.8)[0]["subtitle.speech_covered"].status == QCStatus.PASS
    assert _evaluate(tmp_path, cues, HELLO_WORLD, max_uncovered_speech_sec=0.79)[0]["subtitle.speech_covered"].status == QCStatus.FAIL
    checks, _ = _evaluate(
        tmp_path, cues, HELLO_WORLD, max_uncovered_speech_sec=0.0,
        extra_covered_intervals=[CoveredInterval(start=2.0, end=3.0, label="emphasis graphic")],
    )
    check = checks["subtitle.speech_covered"]
    assert check.status == QCStatus.PASS
    assert check.evidence["extra_covered_intervals"] == [{"start": 2.0, "end": 3.0, "label": "emphasis graphic"}]


def test_partly_covered_word_counts_only_its_uncovered_part(tmp_path):
    # the cue ends at 2.2, inside "how" (2.1-2.3): uncovered speech runs 2.2-2.9
    checks, _ = _evaluate(tmp_path, [(1.0, 2.2, "Hello world how")], HELLO_WORLD, max_uncovered_speech_sec=0.5)
    (violation,) = checks["subtitle.speech_covered"].evidence["violations"]
    assert violation["start"] == 2.2 and violation["duration_sec"] == 0.7


# --- subtitle.no_forbidden_line_start -------------------------------------------

JA_WORDS_POS = [
    ("学校", 1.0, 1.4, {"pos": "noun"}), ("に", 1.4, 1.5, {"pos": "particle"}), ("行き", 1.5, 1.8, {"pos": "verb"}),
    ("ます", 1.8, 2.0, {"pos": "auxiliary"}),
]


def test_pos_part_fails_on_a_particle_at_a_line_start(tmp_path):
    checks, findings = _evaluate(tmp_path, [(1.0, 2.1, "学校\nに行きます")], JA_WORDS_POS, forbidden_line_start_pos=["particle"])
    check = checks["subtitle.no_forbidden_line_start"]
    assert check.status == QCStatus.FAIL
    (violation,) = check.evidence["violations"]
    assert violation["line_number"] == 2 and violation["line"] == "に行きます"
    assert violation["pos"] == "particle" and violation["word"]["text"] == "に"
    assert violation["time_sec"] == 1.0 and violation["cue_index"] == 1
    assert check.evidence["pos_part"] == {"status": "CHECKED"}
    assert findings[0].code == "SUBTITLE_FORBIDDEN_LINE_START"


def test_pos_part_passes_when_lines_start_with_content_words(tmp_path):
    checks, _ = _evaluate(tmp_path, [(1.0, 2.1, "学校に\n行きます")], JA_WORDS_POS, forbidden_line_start_pos=["particle", "auxiliary"])
    assert checks["subtitle.no_forbidden_line_start"].status == QCStatus.PASS


def test_pos_part_is_unknown_without_pos_and_never_guesses_from_characters(tmp_path):
    # the leading "が" of "がっこう" (a noun written in kana) must not be guessed to be a particle
    words = [("がっこう", 1.0, 1.5), ("に", 1.5, 1.6)]
    checks, findings = _evaluate(tmp_path, [(1.0, 2.0, "がっこうに")], words, forbidden_line_start_pos=["particle"])
    check = checks["subtitle.no_forbidden_line_start"]
    assert check.status == QCStatus.UNKNOWN
    assert "no pos" in check.reason
    assert check.evidence["pos_part"]["status"] == "UNKNOWN"
    assert findings == []


def test_pos_part_is_unknown_without_a_transcript(tmp_path):
    checks, _ = _evaluate(tmp_path, [(1.0, 2.0, "に行きます")], forbidden_line_start_pos=["particle"])
    check = checks["subtitle.no_forbidden_line_start"]
    assert check.status == QCStatus.UNKNOWN
    assert "transcript" in check.reason


def test_line_whose_words_do_not_spell_it_is_unverified(tmp_path):
    words = [("学校", 1.0, 1.4, {"pos": "noun"}), ("へ", 1.4, 1.5, {"pos": "particle"})]
    checks, _ = _evaluate(tmp_path, [(1.0, 2.0, "学校に")], words, forbidden_line_start_pos=["particle"])
    check = checks["subtitle.no_forbidden_line_start"]
    assert check.status == QCStatus.UNKNOWN
    assert check.evidence["unverified"][0]["reason"] == "the transcript words in this cue do not spell the cue text"


def test_phrase_part_runs_without_a_transcript_and_reports_while_pos_is_unknown(tmp_path):
    checks, findings = _evaluate(
        tmp_path, [(1.0, 2.0, "学校\nって言った"), (3.0, 4.0, "「を」")],
        forbidden_line_start_pos=["particle"], forbidden_line_start_phrases=["って", "を"],
    )
    check = checks["subtitle.no_forbidden_line_start"]
    assert check.status == QCStatus.FAIL
    assert [(v["cue_index"], v["line_number"], v["phrase"], v["phrase_matched_on"]) for v in check.evidence["violations"]] == [
        (1, 2, "って", "text_prefix"), (2, 1, "を", "text_prefix"),
    ]
    assert check.evidence["pos_part"]["status"] == "UNKNOWN"
    assert check.evidence["scope"] == "each line of each cue"
    assert findings[0].timestamp == 1.0


def test_phrase_must_end_on_a_word_boundary_when_words_are_aligned(tmp_path):
    cues = [(1.0, 2.0, "はい")]
    words = [("はい", 1.0, 1.4, {"pos": "interjection"})]
    # aligned with the transcript: "は" is not a word of its own here
    assert _evaluate(tmp_path, cues, words, forbidden_line_start_phrases=["は"])[0]["subtitle.no_forbidden_line_start"].status == QCStatus.PASS
    # no transcript: literal prefix match
    assert _evaluate(tmp_path, cues, forbidden_line_start_phrases=["は"])[0]["subtitle.no_forbidden_line_start"].status == QCStatus.FAIL


def test_phrase_matching_ignores_case_width_and_punctuation(tmp_path):
    checks, _ = _evaluate(tmp_path, [(1.0, 2.0, "- AND then")], forbidden_line_start_phrases=["and"])
    assert checks["subtitle.no_forbidden_line_start"].status == QCStatus.FAIL
    checks, _ = _evaluate(tmp_path, [(1.0, 2.0, "Android")], [("Android", 1.0, 1.5)], forbidden_line_start_phrases=["and"])
    assert checks["subtitle.no_forbidden_line_start"].status == QCStatus.PASS


# --- character limits ----------------------------------------------------------


def test_total_chars_limit_fails_and_counts_nfkc_code_points_without_markup(tmp_path):
    cues = [(1.0, 2.0, "<i>ｶﾞｷﾞ</i>\nabc"), (3.0, 4.0, "あいうえおか")]
    checks, findings = _evaluate(tmp_path, cues, max_total_chars=5)
    check = checks["subtitle.total_chars_within_limit"]
    assert check.status == QCStatus.FAIL
    # "ｶﾞｷﾞ" is four code points but NFKC "ガギ" is two; "<i>" is markup; the line break is not counted
    assert [(v["cue_index"], v["chars"]) for v in check.evidence["violations"]] == [(2, 6)]
    assert findings[0].code == "SUBTITLE_CUE_TOO_MANY_CHARS"
    assert findings[0].severity.value == "FAIL"


def test_min_cue_chars_is_a_warning_not_a_failure(tmp_path):
    cues = [(1.0, 2.0, "はい"), (3.0, 4.0, "そうですね"), (5.0, 6.0, "<i></i>")]
    checks, findings = _evaluate(tmp_path, cues, min_cue_chars=3)
    check = checks["subtitle.cue_chars_meet_minimum"]
    assert check.status == QCStatus.WARN
    assert [(v["cue_index"], v["chars"], v["time_sec"]) for v in check.evidence["violations"]] == [(1, 2, 1.0)]
    (finding,) = [f for f in findings if f.code == "SUBTITLE_CUE_TOO_FEW_CHARS"]
    assert finding.severity.value == "WARN"
    assert QCStatus.aggregate([c.status for c in checks.values() if c.check_id != "subtitle.no_empty_cues"]) == QCStatus.WARN


def test_char_checks_pass_within_limits(tmp_path):
    checks, findings = _evaluate(tmp_path, [(1.0, 2.0, "Hello world")], max_total_chars=11, min_cue_chars=11)
    assert checks["subtitle.total_chars_within_limit"].status == QCStatus.PASS
    assert checks["subtitle.cue_chars_meet_minimum"].status == QCStatus.PASS
    assert findings == []


# --- presets and defaults ---------------------------------------------------------


def test_no_new_check_runs_by_default(tmp_path):
    checks, _ = _evaluate(tmp_path, [(1.0, 2.0, "が")], HELLO_WORLD)
    assert not {"subtitle.no_forbidden_line_start", "subtitle.total_chars_within_limit", "subtitle.cue_chars_meet_minimum",
                "subtitle.cue_not_early", "subtitle.speech_covered", "subtitle.cue_boundaries_on_word_boundaries"} & set(checks)


def test_ja_preset_fills_only_unset_fields():
    resolved = resolve_subtitle_preset(SubtitleRule(preset="ja", max_total_chars=40))
    assert resolved.max_total_chars == 40
    assert resolved.min_cue_chars == SUBTITLE_PRESETS["ja"]["min_cue_chars"]
    assert resolved.forbidden_line_start_pos == ["particle", "auxiliary"]
    assert "と思います" in resolved.forbidden_line_start_phrases
    assert resolved.max_cue_lead_sec is None  # timing thresholds are never preset
    assert resolve_subtitle_preset(SubtitleRule()) == SubtitleRule()


def test_ja_preset_end_to_end_on_measurements(tmp_path):
    checks, _ = _evaluate(tmp_path, [(1.0, 2.1, "学校\nに行きます")], JA_WORDS_POS, preset="ja")
    assert checks["subtitle.no_forbidden_line_start"].status == QCStatus.FAIL
    assert checks["subtitle.total_chars_within_limit"].status == QCStatus.PASS


def test_checks_are_unknown_when_cue_texts_were_not_measured(tmp_path):
    srt = _write_srt(tmp_path / "subs.srt", [(1.0, 2.0, "Hello")])
    mmap = {m.id: m for m in measure_subtitle(srt)}
    checks, _ = evaluate_subtitle(mmap, SubtitleRule(max_total_chars=10))
    assert {c.check_id: c for c in checks}["subtitle.total_chars_within_limit"].status == QCStatus.UNKNOWN


# --- request validation -------------------------------------------------------------


@pytest.mark.parametrize("rule", [
    {"preset": "fr"},
    {"forbidden_line_start_pos": ["particles"]},
    {"forbidden_line_start_phrases": ["", "を"]},
    {"word_boundary_tolerance_sec": -0.1},
    {"max_total_chars": 0},
    {"min_cue_chars": 2.5},
    {"extra_covered_intervals": [{"start": 2.0, "end": 1.0}]},
    {"extra_covered_intervals": [{"start": 1.0, "end": 2.0, "colour": "red"}]},
    {"transcript": ""},
])
def test_invalid_subtitle_rule_fields_are_rejected(rule):
    with pytest.raises(QCError) as exc:
        parse_request({"operation": "check", "kind": "subtitle", "input": "subs.srt", "rules": {"subtitle": rule}})
    assert exc.value.code == "INVALID_REQUEST"


def test_pos_list_is_canonicalised():
    request = parse_request({
        "operation": "check", "kind": "subtitle", "input": "subs.srt",
        "rules": {"subtitle": {"forbidden_line_start_pos": ["auxiliary", "particle"]}},
    })
    assert request.subtitle_rule.forbidden_line_start_pos == ["particle", "auxiliary"]


# --- end to end ---------------------------------------------------------------------


def _write_inputs(directory, transcript_words=HELLO_WORLD):
    directory.mkdir(parents=True, exist_ok=True)
    srt = _write_srt(directory / "subs.srt", [(0.2, 2.0, "Hello world")])
    transcript = directory / "transcript.json"
    transcript.write_text(json.dumps(_transcript_doc(transcript_words, key="word", with_index=True)), encoding="utf-8")
    return srt, transcript


def test_end_to_end_subtitle_kind_with_transcript(workspace):
    srt, transcript = _write_inputs(workspace / "in")
    doc = {
        "operation": "check", "kind": "subtitle", "input": str(srt),
        "rules": {"subtitle": {"transcript": str(transcript), "max_cue_lead_sec": 0.5, "max_uncovered_speech_sec": 0.5}},
    }
    resp = run(doc, workspace)
    checks = checks_by_id(resp)
    assert checks["subtitle.cue_not_early"]["status"] == "FAIL"
    assert checks["subtitle.speech_covered"]["status"] == "FAIL"
    m = measurements_by_id(resp)
    assert m["subtitle.transcript_word_count"]["value"] == 5
    assert m["subtitle.transcript_words"]["value"][0]["index"] == 0
    assert m["subtitle.cue_texts"]["value"][0]["lines"] == ["Hello world"]
    assert resp["provenance"]["transcript_input"]["fingerprint"]
    json.dumps(resp)  # the whole report stays JSON
    finding = next(f for f in resp["report"]["findings"] if f["code"] == "SUBTITLE_CUE_EARLY")
    assert finding["timestamp"] == 0.2
    assert finding["evidence"]["violations"][0]["description"].startswith("0.200s cue #1")


def test_cue_texts_are_not_measured_unless_a_rule_asks(workspace):
    srt, _ = _write_inputs(workspace / "in")
    resp = run({"operation": "check", "kind": "subtitle", "input": str(srt)}, workspace)
    assert "subtitle.cue_texts" not in measurements_by_id(resp)


def test_identity_follows_transcript_content_not_its_path(workspace):
    srt, transcript = _write_inputs(workspace / "a")
    moved = workspace / "b" / "elsewhere.json"
    moved.parent.mkdir()
    shutil.copy(transcript, moved)

    def key(path):
        doc = {"operation": "check", "kind": "subtitle", "input": str(srt), "cache_policy": "bypass",
               "rules": {"subtitle": {"transcript": str(path), "max_cue_lead_sec": 0.5}}}
        return run(doc, workspace)["cache"]["key"]

    assert key(transcript) == key(moved)
    moved.write_text(json.dumps(_transcript_doc(HELLO_WORLD[:2])), encoding="utf-8")
    assert key(transcript) != key(moved)


def test_missing_transcript_file_is_a_request_error(workspace):
    srt, _ = _write_inputs(workspace / "in")
    doc = {"operation": "check", "kind": "subtitle", "input": str(srt),
           "rules": {"subtitle": {"transcript": str(workspace / "nope.json"), "max_cue_lead_sec": 0.5}}}
    with pytest.raises(QCError) as exc:
        run(doc, workspace)
    assert exc.value.code == "MISSING_INPUT"


def test_transcript_outside_the_allowed_roots_is_refused(workspace, tmp_path_factory):
    srt, _ = _write_inputs(workspace / "in")
    _, outside = _write_inputs(tmp_path_factory.mktemp("outside"))
    doc = {"operation": "check", "kind": "subtitle", "input": str(srt),
           "rules": {"subtitle": {"transcript": str(outside), "max_cue_lead_sec": 0.5}}}
    with pytest.raises(QCError) as exc:
        run(doc, workspace, allowed_roots=[str(workspace)])
    assert exc.value.code == "PATH_NOT_ALLOWED"


def test_end_to_end_delivery_package_subtitle_with_transcript(workspace):
    srt, transcript = _write_inputs(workspace / "in")
    doc = {
        "operation": "check", "kind": "delivery_package",
        "artifacts": [{"artifact_id": "subs", "artifact_type": "subtitle", "path": str(srt)}],
        "rules": {"delivery_package": {"artifacts": [
            {"artifact_id": "subs", "subtitle": {"transcript": str(transcript), "max_cue_lead_sec": 0.5}},
        ]}},
    }
    resp = run(doc, workspace)
    early = [c for c in resp["report"]["checks"] if c["check_id"] == "subtitle.cue_not_early"]
    assert [c["status"] for c in early] == ["FAIL"]
    assert early[0]["artifact_id"] == "subs"
    assert resp["provenance"]["artifacts"][0]["transcript_fingerprint"]
