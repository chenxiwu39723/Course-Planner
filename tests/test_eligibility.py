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


# ---------------------------------------------------------- 真實備註寫法（#6–#8）
import pytest  # noqa: E402

from planner.eligibility import _dept_match  # noqa: E402


@pytest.mark.parametrize("notes, expected", [
    ("限二年級，初選者需經教練同意", (2, 2)),                     # 限定單一年級，不是「以上」
    ("限三、四年級，初選者需經教練同意", (3, 4)),
    ("限二三四年級未修過籃球入門者", (2, 4)),
    ("分發條件:(1)限大二、大三、大四(2)第一次上課", (2, 4)),
    ("年級:限二年級、三年級、四年級、五年級", (2, 5)),
    ("電機系三四年級可修。選修此門課", (3, 4)),
    ("僅供電機系大一學生選課，電機系大二(含)以上及外系學生若欲選課需密碼卡", (1, 1)),
    ("限大三以上選修", (3, None)),
    ("三年級(含)以上", (3, None)),
    ("先修流體力學,大三可選修", (3, None)),                     # 沒寫「限」→ 只當下限
    ("限二三四年級未修過桌球入門者。研究生選修請至體育室加簽", (2, None)),
    ("大學部限四年級可選修 [碩博同修]", (4, None)),               # 不排除同修研究生
    ("1.限生科系生物醫學組博士生二年級", (None, None)),            # 研究生年級，非大學部
    ("115年準大一生暑期先修課程，限準大一生。", (None, None)),
    ("樓204室,一年級外籍生必修", (None, None)),
])
def test_keyword_parse_grade_range(notes, expected):
    c = keyword_parse(notes)
    assert (c.min_grade, c.max_grade) == expected


def test_match_grade_range():
    cond = EligibilityCondition(min_grade=2, max_grade=2, confidence="high")
    assert match(cond, UserProfile(grade=2)).status == MATCH
    r = match(cond, UserProfile(grade=4))
    assert r.status == MAYBE_NOT and "限2年級" in r.reasons[0]


@pytest.mark.parametrize("notes, prereqs, other", [
    ("不列入畢業學分，限修畢兩門大一體育及三門體育必修興趣選課四年級且未修過籃球入門之學生",
     [], ["先修：兩門大一體育及三門體育必修興趣選課"]),
    ("中級課程，限修畢西班牙文一A及一B的同學選修", ["西班牙文一A及一B"], []),
    ("先修EG1001工程程式設計", ["工程程式設計"], []),
    ("議修習CM1010前，須先修習CM1009", ["CM1009"], []),
    ("建議先修(ME2071)熱力學", [], ["建議先修：熱力學"]),
    ("同修(含先修)GP3083台灣地質,本課程", [], ["同修或先修：台灣地質"]),
    ("請先修自動控制Ⅱ(ME4062)或線性系統(ME7056)", ["自動控制Ⅱ或線性系統"], []),
])
def test_keyword_parse_prerequisite_boundaries(notes, prereqs, other):
    c = keyword_parse(notes)
    assert c.prerequisites == prereqs
    assert [o for o in c.other_conditions if "修" in o] == other


def test_prerequisite_alternatives_any_one():
    cond = EligibilityCondition(prerequisites=["漢語語法學或華語文教材教法"], confidence="high")
    assert match(cond, UserProfile(completed_courses=["華語文教材教法"])).status == MATCH


def test_keyword_parse_unnamed_dept_goes_to_other():
    c = keyword_parse("限本所博士生修習")
    assert c.allowed_depts == [] and "限本所" in c.other_conditions


@pytest.mark.parametrize("user, allowed, ok", [
    ("資訊工程學系", "資工系", True),
    ("資訊管理學系", "資管系", True),
    ("大氣科學學系", "大氣系", True),
    ("生命科學系", "生科所", True),
    ("化學學系", "化學系", True),
    ("數學系", "數學系計算與資料科學組", True),
    ("化學學系", "化學工程與材料工程學系", False),      # 不可子字串誤配
    ("資訊工程學系", "非資訊工程學系", False),          # 「非X系」= 排除
    ("企業管理學系", "非資訊管理學系", True),
])
def test_dept_match_aliases(user, allowed, ok):
    assert _dept_match(user, allowed) is ok
