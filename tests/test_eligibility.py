# -*- coding: utf-8 -*-
"""限修條件解析與比對、以及 llm_client 的測試。"""
from planner.llm_client import LLMClient, strip_json_fences, parse_json_object
from planner.eligibility import (
    keyword_parse, parse_notes, match, EligibilityCondition, UserProfile,
    evaluate, _coerce_condition, MATCH, MAYBE_NOT, UNKNOWN,
)


# ---------------------------------------------------------- llm_client
def test_strip_json_fences():
    assert strip_json_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert strip_json_fences('前言 {"a": 1} 後語') == '{"a": 1}'


def test_parse_json_object_ok_and_fail():
    assert parse_json_object('{"x": 2}') == {"x": 2}
    import pytest
    with pytest.raises(ValueError):
        parse_json_object("[1,2,3]")  # 不是物件


def test_mock_client_returns_fallback():
    client = LLMClient(provider="mock")
    fb = {"min_grade": 3}
    assert client.complete_json([{"role": "user", "content": "x"}], fallback=fb) == fb
    assert client.complete_json([], fallback=None) == {}


# ---------------------------------------------------------- keyword_parse
def test_keyword_parse_grade():
    assert keyword_parse("限大三以上選修").min_grade == 3
    assert keyword_parse("三年級(含)以上").min_grade == 3


def test_keyword_parse_dept():
    c = keyword_parse("限資訊工程學系學生選修")
    assert any("資訊工程" in d for d in c.allowed_depts)


def test_keyword_parse_prerequisite():
    c = keyword_parse("須先修微積分(一)")
    assert "微積分(一)" in c.prerequisites


def test_keyword_parse_ignores_generic_prereq_word():
    # 「先修課程」不應被當成先修課「課程」
    c = keyword_parse("115年準大一生暑期先修課程，限準大一生。")
    assert "課程" not in c.prerequisites


def test_keyword_parse_other_conditions():
    c = keyword_parse("須經教師同意 [不開放初選] [密碼卡:全部使用]")
    assert "經教師同意" in c.other_conditions
    assert "不開放初選" in c.other_conditions
    assert any("密碼卡" in o for o in c.other_conditions)


def test_keyword_parse_empty():
    assert keyword_parse("").is_empty()
    assert keyword_parse("   ").is_empty()


# ---------------------------------------------------------- schema 驗證
def test_coerce_condition_valid():
    c = _coerce_condition({"min_grade": "3", "allowed_depts": "通訊系",
                           "prerequisites": ["微積分"], "confidence": "HIGH"})
    assert c.min_grade == 3
    assert c.allowed_depts == ["通訊系"]
    assert c.confidence == "high"


def test_coerce_condition_out_of_range_grade():
    assert _coerce_condition({"min_grade": 99}).min_grade is None


def test_coerce_condition_bad_input():
    assert _coerce_condition("not a dict") is None


def test_parse_notes_with_mock_falls_back_to_keyword():
    client = LLMClient(provider="mock")
    cond = parse_notes("限大二以上，須先修線性代數", client)
    assert cond.min_grade == 2
    assert "線性代數" in cond.prerequisites


def test_parse_notes_empty_is_high_confidence_empty():
    client = LLMClient(provider="mock")
    cond = parse_notes("", client)
    assert cond.is_empty() and cond.confidence == "high"


# ---------------------------------------------------------- match 三狀態
def test_match_empty_condition_is_match():
    r = match(EligibilityCondition(confidence="high"), UserProfile(grade=1))
    assert r.status == MATCH


def test_match_grade_pass_and_fail():
    cond = EligibilityCondition(min_grade=3, confidence="high")
    assert match(cond, UserProfile(grade=3)).status == MATCH
    assert match(cond, UserProfile(grade=2)).status == MAYBE_NOT


def test_match_grade_unknown_when_no_user_grade():
    cond = EligibilityCondition(min_grade=3, confidence="high")
    assert match(cond, UserProfile(grade=None)).status == UNKNOWN


def test_match_dept_fuzzy():
    cond = EligibilityCondition(allowed_depts=["通訊系"], confidence="high")
    assert match(cond, UserProfile(dept="通訊工程學系")).status == MATCH
    assert match(cond, UserProfile(dept="資訊工程學系")).status == MAYBE_NOT


def test_match_prerequisite_taken_vs_missing():
    cond = EligibilityCondition(prerequisites=["微積分"], confidence="high")
    assert match(cond, UserProfile(completed_courses=["微積分(一)"])).status == MATCH
    assert match(cond, UserProfile(completed_courses=[])).status == MAYBE_NOT


def test_match_other_conditions_unknown():
    cond = EligibilityCondition(other_conditions=["需經教師同意"], confidence="medium")
    assert match(cond, UserProfile(grade=3)).status == UNKNOWN


def test_match_hard_fail_beats_unknown():
    # 年級不符(硬) + 其他條件(不確定) → 可能不符合
    cond = EligibilityCondition(min_grade=3, other_conditions=["需同意"], confidence="high")
    assert match(cond, UserProfile(grade=1)).status == MAYBE_NOT


def test_match_keeps_raw_notes():
    r = match(EligibilityCondition(confidence="high"), UserProfile(), raw_notes="原始備註")
    assert r.raw_notes == "原始備註"


# ---------------------------------------------------------- evaluate 用快取
def test_evaluate_uses_cache():
    cache = {"C1": EligibilityCondition(min_grade=2, confidence="high")}
    r = evaluate("C1", "限大二以上", UserProfile(grade=1), cache)
    assert r.status == MAYBE_NOT


def test_evaluate_missing_cache_falls_back_to_keyword():
    r = evaluate("NOPE", "限大三以上", UserProfile(grade=1), {})
    assert r.status == MAYBE_NOT
