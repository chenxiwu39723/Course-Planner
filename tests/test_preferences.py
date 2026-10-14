# -*- coding: utf-8 -*-
"""白話偏好解析測試。"""
from planner.llm_client import LLMClient
from planner.preferences import (
    keyword_parse, parse_preferences, _coerce_spec, PreferenceSpec,
)


def test_keyword_parse_early_morning_avoided():
    spec = keyword_parse("不要早八")
    # 週一到週五的第 1 節都要避開
    assert sorted(spec.avoid_periods) == [[d, "1"] for d in (1, 2, 3, 4, 5)]


def test_keyword_parse_free_day():
    spec = keyword_parse("週五想空著")
    assert spec.free_days == [5]


def test_keyword_parse_free_day_needs_empty_intent():
    # 只提到星期、沒有想空的意思 → 不應列入 free_days
    spec = keyword_parse("星期五有一堂必修")
    assert spec.free_days == []


def test_keyword_parse_credit_range():
    spec = keyword_parse("想修 6 到 10 學分")
    assert spec.min_credits == 6 and spec.max_credits == 10


def test_keyword_parse_credit_min_max_separate():
    assert keyword_parse("至少 16 學分").min_credits == 16
    assert keyword_parse("最多 20 學分").max_credits == 20


def test_keyword_parse_language():
    spec = keyword_parse("想學日文")
    assert "日語" in spec.languages


def test_keyword_parse_topics_dedup():
    spec = keyword_parse("想學資料分析相關")
    assert spec.topics == ["資料分析"]


def test_keyword_parse_empty():
    spec = keyword_parse("")
    assert spec.topics == [] and spec.avoid_periods == []


def test_coerce_spec_filters_bad_slots():
    spec = _coerce_spec({"avoid_periods": [[1, "1"], [9, "1"], [1, "X"]],
                         "free_days": [5, 9], "min_credits": "6",
                         "languages": ["日文"]})
    assert spec.avoid_periods == [[1, "1"]]      # 星期9、節次X 被濾掉
    assert spec.free_days == [5]
    assert spec.min_credits == 6.0
    assert spec.languages == ["日語"]            # 別名正規化


def test_parse_preferences_mock_uses_keyword():
    spec = parse_preferences("不要早八、週五想空著", LLMClient(provider="mock"))
    assert spec.free_days == [5]
    assert [1, "1"] in spec.avoid_periods


def test_to_solver_prefs_conversion():
    spec = PreferenceSpec(min_credits=6, max_credits=10,
                          avoid_periods=[[1, "1"]], free_days=[5],
                          ge_domains=["自然"], languages=["日語"], topics=["資料分析"])
    p = spec.to_solver_prefs()
    assert p.min_credits == 6 and p.max_credits == 10
    assert p.avoid_periods == [(1, "1")]
    assert p.free_days == [5]
    assert p.ge_domains == ["自然"] and p.languages == ["日語"]
