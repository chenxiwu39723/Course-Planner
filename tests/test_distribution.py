# -*- coding: utf-8 -*-
"""分發條件解析與整合測試（不涉網路）。"""
import json

from planner.distribution import parse_distribution, to_condition, raw_text
from planner.eligibility import (
    EligibilityCondition, merge_conditions, load_distribution, evaluate,
    keyword_parse, UserProfile, MATCH, MAYBE_NOT,
)

_HTML = """
<html><body><table>
  <tr><th>優先順序</th><th>相關條件限制說明</th></tr>
  <tr><td>1</td><td>系所:限資訊工程學系。</td></tr>
  <tr><td>2</td><td>年級:限三年級以上。</td></tr>
  <tr><td>3</td><td>身份:限申請校學士。</td></tr>
</table></body></html>
"""


def test_parse_distribution_rows():
    rows = parse_distribution(_HTML)
    assert [r["priority"] for r in rows] == [1, 2, 3]
    assert rows[0]["category"] == "系所"
    assert rows[0]["description"] == "限資訊工程學系"
    assert "1. 系所:限資訊工程學系" in raw_text(rows)


def test_to_condition_maps_structured_fields():
    cond = to_condition(parse_distribution(_HTML))
    assert cond.confidence == "high"
    assert "資訊工程學系" in cond.allowed_depts
    assert cond.min_grade == 3
    # 身份限制無法以系所/年級確認 → 放進 other_conditions
    assert any("校學士" in o for o in cond.other_conditions)


def test_to_condition_empty_page():
    cond = to_condition([])
    assert cond.is_empty()
    assert cond.confidence == "high"


def test_merge_prefers_distribution_grade_and_unions():
    notes = EligibilityCondition(min_grade=None, prerequisites=["微積分"],
                                 other_conditions=["經教師同意"], confidence="low")
    dist = EligibilityCondition(min_grade=3, allowed_depts=["資訊工程學系"],
                                confidence="high")
    merged = merge_conditions(notes, dist)
    assert merged.min_grade == 3
    assert "資訊工程學系" in merged.allowed_depts
    assert "微積分" in merged.prerequisites          # 備註的先修保留
    assert "經教師同意" in merged.other_conditions
    assert merged.confidence == "high"


def test_merge_empty_distribution_keeps_notes():
    notes = EligibilityCondition(prerequisites=["資料結構"], confidence="medium")
    merged = merge_conditions(notes, EligibilityCondition(confidence="high"))
    assert merged is notes


def test_load_distribution_and_evaluate(tmp_path):
    cache_file = tmp_path / "distribution_cache.json"
    cond = to_condition(parse_distribution(_HTML))
    cache_file.write_text(json.dumps({
        "conditions": {"00001": {"condition": cond.to_dict(), "raw": "", "rows": []}}
    }, ensure_ascii=False), encoding="utf-8")

    dist = load_distribution(cache_file)
    assert "00001" in dist and dist["00001"].min_grade == 3

    # 年級不足的使用者 → 可能不符合
    user = UserProfile(dept="資訊工程學系", grade=1)
    res = evaluate("00001", "", user, cache={}, distribution=dist)
    assert res.status == MAYBE_NOT

    # 系所相符、年級足夠 → 身份限制使其落在非「符合」（需人工確認）
    user2 = UserProfile(dept="資訊工程學系", grade=3)
    res2 = evaluate("00001", "", user2, cache={}, distribution=dist)
    assert res2.status != MATCH   # 仍有「限申請校學士」需人工確認
