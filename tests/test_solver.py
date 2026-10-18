# -*- coding: utf-8 -*-
"""候選課篩選與 solver 的測試（含『不會產生衝堂課表』的保證）。"""
import pytest

from planner import timeslots as ts
from planner.models import Course
from planner.solver import (
    Preferences, compute_free_slots, candidate_courses, solve, ge_domain_matches,
)
from planner.data_loader import load_courses


# ---- 小型合成資料，確保測試確定、與線上資料無關 ----
def ge(cid, times, domain, credits=3.0, cap=50):
    return Course(id=cid, code=cid, name=cid, teacher=[], dept="通識教育中心",
                  credits=credits, times=times, category="選修",
                  ge_domain=domain, capacity=cap)


def test_ge_domain_matches_single_and_multi():
    # 單一領域（範例資料）
    assert ge_domain_matches("人文與思想領域", ["人文與思想領域"])
    # 多領域以「、」串接（真實資料）：命中其中之一即可
    assert ge_domain_matches("文化與社會、第二外語", ["第二外語"])
    assert ge_domain_matches("基礎英文、應用英文、學術語文", ["學術語文"])
    # 未命中 / 空值
    assert not ge_domain_matches("文化與社會、第二外語", ["自然科學領域"])
    assert not ge_domain_matches(None, ["第二外語"])
    assert not ge_domain_matches("第二外語", None)


def lang(cid, times, language, credits=3.0):
    return Course(id=cid, code=cid, name=cid, teacher=[], dept="語言中心",
                  credits=credits, times=times, category="選修",
                  is_language=True, language=language)


def major(cid, times, credits=3.0):
    return Course(id=cid, code=cid, name=cid, teacher=[], dept="資訊工程學系",
                  credits=credits, times=times, category="必修")


@pytest.fixture
def world():
    return [
        major("M1", [(1, "2"), (1, "3")], 3),          # 固定課
        ge("G1", [(2, "2"), (2, "3")], "人文", 3),
        ge("G2", [(1, "2"), (1, "4")], "人文", 2),       # 與 M1 衝堂((1,'2'))
        ge("G3", [(3, "5"), (3, "6")], "社會", 3),
        ge("G4", [(4, "7")], "自然", 1),
        ge("G5", [(2, "2")], "自然", 2),                 # 與 G1 衝堂((2,'2'))
        lang("L1", [(5, "2"), (5, "3")], "日語", 3),
        ge("G6", [], "社會", 3),                         # 無時間 → 不應入候選
    ]


# ---------------------------------------------------------- 空堂
def test_compute_free_slots_excludes_fixed_and_avoid(world):
    prefs = Preferences(days=[1, 2], periods=["2", "3", "4"],
                        avoid_periods=[(2, "4")])
    free = compute_free_slots(world, ["M1"], prefs)
    # 範圍 2天×3節=6；扣 M1 的(1,2)(1,3) 與 avoid(2,4) → 剩 3 格
    assert (1, "2") not in free and (1, "3") not in free
    assert (2, "4") not in free
    assert free == {(1, "4"), (2, "2"), (2, "3")}


def test_compute_free_slots_free_day(world):
    prefs = Preferences(days=[1, 5], periods=["2", "3"], free_days=[5])
    free = compute_free_slots(world, [], prefs)
    assert all(d != 5 for d, _ in free)


# ---------------------------------------------------------- 候選課
def test_candidates_only_ge_and_language_in_free_no_conflict(world):
    fixed = [c for c in world if c.id == "M1"]
    free = compute_free_slots(world, ["M1"], Preferences())
    cands = candidate_courses(world, fixed, free, Preferences())
    ids = {c.id for c in cands}
    assert "M1" not in ids               # 固定課不在候選
    assert "G2" not in ids               # 與固定課衝堂
    assert "G6" not in ids               # 無時間
    assert {"G1", "G3", "G4", "G5", "L1"} <= ids


def test_candidates_filter_by_domain(world):
    fixed = []
    free = compute_free_slots(world, [], Preferences())
    prefs = Preferences(ge_domains=["自然"], include_language=False)
    cands = candidate_courses(world, fixed, free, prefs)
    assert {c.id for c in cands} == {"G4", "G5"}


def test_candidates_language_only(world):
    free = compute_free_slots(world, [], Preferences())
    prefs = Preferences(include_ge=False, include_language=True)
    cands = candidate_courses(world, [], free, prefs)
    assert {c.id for c in cands} == {"L1"}


# ---------------------------------------------------------- solver
def test_solve_respects_credit_range_and_includes_fixed(world):
    prefs = Preferences(min_credits=6, max_credits=9)
    schedules, stats = solve(world, ["M1"], prefs, max_schedules=3)
    assert schedules, "應至少有一組可行課表"
    for s in schedules:
        assert 6 <= s.total_credits <= 9
        assert any(c.id == "M1" for c in s.courses)   # 必含固定課


def test_solve_never_produces_conflicts(world):
    prefs = Preferences(min_credits=4, max_credits=10)
    schedules, _ = solve(world, ["M1"], prefs, max_schedules=5)
    for s in schedules:
        assert ts.any_conflict(s.courses) == [], f"課表不得衝堂：{[c.id for c in s.courses]}"


def test_solve_at_most_max_schedules(world):
    prefs = Preferences(min_credits=1, max_credits=12)
    schedules, _ = solve(world, [], prefs, max_schedules=3)
    assert len(schedules) <= 3


def test_solve_ranked_by_score_desc(world):
    prefs = Preferences(min_credits=1, max_credits=12, ge_domains=["自然"])
    schedules, _ = solve(world, [], prefs, max_schedules=3)
    scores = [s.score for s in schedules]
    assert scores == sorted(scores, reverse=True)


def test_solve_semantic_score_influences_choice(world):
    # 給 G3 很高的語意分，應讓含 G3 的課表排在前面
    prefs = Preferences(min_credits=3, max_credits=3)  # 只能選一門 3 學分
    schedules, _ = solve(world, [], prefs, max_schedules=3,
                         score_fn=lambda c: 100.0 if c.id == "G3" else 0.0)
    assert schedules[0].added[0].id == "G3"


def test_solve_hits_limit_gracefully(world):
    # 極小 node_limit：不應報錯，仍回傳 stats.hit_limit
    prefs = Preferences(min_credits=1, max_credits=12)
    schedules, stats = solve(world, [], prefs, node_limit=1)
    assert stats.hit_limit is True
    assert isinstance(schedules, list)


def test_solve_avoids_specified_periods(world):
    # 避開 (3,'5') → 含該節的 G3 不得出現在任何課表
    prefs = Preferences(min_credits=1, max_credits=12, avoid_periods=[(3, "5")])
    schedules, _ = solve(world, [], prefs, max_schedules=5)
    for s in schedules:
        assert all(c.id != "G3" for c in s.added)


# ---------------------------------------------------------- 以範例資料做煙霧測試
def test_solve_on_sample_data_no_conflicts():
    courses, src = load_courses("sample")
    assert src == "sample" and courses
    # 固定一門必修，排通識 4~8 學分
    fixed = next(c for c in courses if c.category == "必修" and c.times)
    prefs = Preferences(min_credits=fixed.credits + 4,
                        max_credits=fixed.credits + 8,
                        include_language=False)
    schedules, stats = solve(courses, [fixed.id], prefs, max_schedules=3,
                             time_limit=2.0, node_limit=10_000)
    assert schedules, "範例資料應能排出課表"
    for s in schedules:
        assert ts.any_conflict(s.courses) == []
        assert prefs.min_credits <= s.total_credits <= prefs.max_credits
