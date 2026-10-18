# -*- coding: utf-8 -*-
"""課表組合求解（階段 2，非 AI 核心）。

全部為確定性程式：空堂計算、候選課篩選、回溯搜尋排課。
語言模型不參與任何時間/衝堂判斷（符合 CLAUDE.md 核心原則）。

主要 API：
- compute_free_slots(all_courses, fixed_ids, prefs)   一週空堂格
- candidate_courses(all_courses, fixed, free, prefs)  可填入的通識/語言候選課
- solve(all_courses, fixed_ids, prefs, ...)           最多 3 組課表，依分數排序
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .models import Course, TimeSlot
from . import timeslots as ts


# ---------------------------------------------------------------- 偏好/條件
@dataclass
class Preferences:
    """使用者偏好與硬性條件（階段 4 會由白話文字解析成這個結構）。"""

    min_credits: float = 0.0
    max_credits: float = 999.0
    avoid_periods: list[TimeSlot] = field(default_factory=list)   # 硬性：避開的 (day, period)
    free_days: list[int] = field(default_factory=list)           # 硬性：整天空著的星期碼
    ge_domains: list[str] | None = None                          # 篩選：只收這些通識領域
    languages: list[str] | None = None                          # 篩選：只收這些授課語言
    include_ge: bool = True                                      # 候選是否含通識課
    include_language: bool = True                                # 候選是否含語言課
    days: list[int] = field(default_factory=lambda: list(ts.DEFAULT_DAYS))
    periods: list[str] = field(default_factory=lambda: list(ts.DEFAULT_PERIODS))


@dataclass
class Schedule:
    """一組課表結果。"""

    fixed: list[Course]
    added: list[Course]
    total_credits: float
    score: float
    reasons: dict[str, str] = field(default_factory=dict)  # 課號 → 推薦理由（階段4強化）

    @property
    def courses(self) -> list[Course]:
        return self.fixed + self.added


# ScoreFn：給一門候選課一個語意相關度分數（階段 4 由向量搜尋提供），預設 0。
ScoreFn = Callable[[Course], float]


# ---------------------------------------------------------------- 空堂
def _lookup(all_courses: Iterable[Course]) -> dict[str, Course]:
    return {c.id: c for c in all_courses}


def compute_free_slots(
    all_courses: Iterable[Course],
    fixed_ids: Iterable[str],
    prefs: Preferences | None = None,
) -> set[TimeSlot]:
    """回傳一週空堂格：在 days×periods 範圍內，扣掉固定課與硬性避開/空堂日。"""
    prefs = prefs or Preferences()
    index = _lookup(all_courses)
    fixed = [index[i] for i in fixed_ids if i in index]

    grid = {(d, p) for d in prefs.days for p in prefs.periods}
    grid -= ts.occupied_slots(fixed)                       # 扣固定課
    grid -= set(prefs.avoid_periods)                       # 扣避開節次
    grid -= {(d, p) for d in prefs.free_days for p in prefs.periods}  # 扣整天空堂日
    return grid


# ---------------------------------------------------------------- 候選課
def candidate_courses(
    all_courses: Iterable[Course],
    fixed: Iterable[Course],
    free: set[TimeSlot],
    prefs: Preferences | None = None,
) -> list[Course]:
    """可填入的候選課：通識/語言課、時間全落在空堂、且不與固定課衝堂。

    另可依通識領域（ge_domains）、授課語言（languages）篩選。
    """
    prefs = prefs or Preferences()
    fixed_list = list(fixed)
    fixed_ids = {c.id for c in fixed_list}
    fixed_slots = ts.occupied_slots(fixed_list)

    out: list[Course] = []
    for c in all_courses:
        if c.id in fixed_ids:
            continue
        is_ge = c.ge_domain is not None
        # 判定是否屬於要納入的候選類型
        type_ok = (prefs.include_ge and is_ge) or (prefs.include_language and c.is_language)
        if not type_ok:
            continue
        if not c.times:                                   # 無時間資訊者不納入排課
            continue
        if not set(c.times).issubset(free):               # 必須全在空堂內
            continue
        if set(c.times) & fixed_slots:                    # 不與固定課衝堂（雙重保險）
            continue
        if prefs.ge_domains and not ge_domain_matches(c.ge_domain, prefs.ge_domains):
            continue
        if prefs.languages and (c.language not in prefs.languages):
            continue
        out.append(c)
    return out


# ---------------------------------------------------------------- 通識領域比對
def ge_domain_matches(course_domain: str | None, wanted: list[str] | None) -> bool:
    """課程的通識領域是否命中使用者想要的領域之一。

    真實資料的 ge_domain 可能是多領域以「、」串接（如「文化與社會、第二外語」），
    因此以拆分後的集合相交判定；範例資料的單一領域值為其特例，向下相容。
    """
    if not wanted:
        return False
    if not course_domain:
        return False
    parts = {p.strip() for p in course_domain.split("、") if p.strip()}
    return bool(parts & set(wanted))


# ---------------------------------------------------------------- 分數
def preference_score(course: Course, prefs: Preferences) -> float:
    """偏好符合度分數（確定性）。語意相關度由 solve 的 score_fn 另外加。"""
    s = 0.0
    if ge_domain_matches(course.ge_domain, prefs.ge_domains):
        s += 2.0
    if prefs.languages and course.language in prefs.languages:
        s += 2.0
    # 輕微偏好有明確時間、人數較多（較可能選得到）的課
    if course.capacity and course.capacity >= 40:
        s += 0.2
    return s


# ---------------------------------------------------------------- 回溯求解
@dataclass
class SolveStats:
    nodes: int = 0
    elapsed: float = 0.0
    hit_limit: bool = False


def solve(
    all_courses: Iterable[Course],
    fixed_ids: Iterable[str],
    prefs: Preferences | None = None,
    *,
    score_fn: ScoreFn | None = None,
    max_schedules: int = 3,
    max_added: int | None = None,
    time_limit: float = 2.0,
    node_limit: int = 10_000,
) -> tuple[list[Schedule], SolveStats]:
    """以回溯搜尋產生最多 max_schedules 組課表，依分數由高到低排序。

    硬性條件：不衝堂、總學分落在 [min_credits, max_credits]、避開指定節次/空堂日、
    必含固定課（fixed_ids）。
    max_added：最多額外加入幾門通識/語言課（None=不限）。因為分數隨課數單調遞增，
    使用者若未設學分上限，需靠此參數避免把整週空堂塞滿（排出 40 學分的誇張課表）。
    搜尋設上限（time_limit 秒 或 node_limit 節點），超過時回傳目前最佳結果。
    回傳 (schedules, stats)。
    """
    prefs = prefs or Preferences()
    score_fn = score_fn or (lambda _c: 0.0)
    index = _lookup(all_courses)
    all_list = list(index.values())

    fixed = [index[i] for i in fixed_ids if i in index]
    fixed_credits = sum(c.credits for c in fixed)

    free = compute_free_slots(all_list, [c.id for c in fixed], prefs)
    cands = candidate_courses(all_list, fixed, free, prefs)

    # 每門候選的分數（偏好 + 語意），並依分數排序，讓好的組合較早被找到
    cand_score: dict[str, float] = {
        c.id: preference_score(c, prefs) + score_fn(c) for c in cands
    }
    cands.sort(key=lambda c: (-cand_score[c.id], c.id))
    cand_slots: dict[str, set[TimeSlot]] = {c.id: set(c.times) for c in cands}

    # 剩餘可加學分（供剪枝：若加完後面全部仍達不到 min 就不必往下）
    suffix_credits = [0.0] * (len(cands) + 1)
    for i in range(len(cands) - 1, -1, -1):
        suffix_credits[i] = suffix_credits[i + 1] + cands[i].credits

    start = time.monotonic()
    stats = SolveStats()
    found: list[tuple[float, tuple[str, ...], list[Course]]] = []
    seen: set[frozenset[str]] = set()

    def record(selected: list[Course]) -> None:
        total = fixed_credits + sum(c.credits for c in selected)
        if not (prefs.min_credits <= total <= prefs.max_credits):
            return
        key = frozenset(c.id for c in selected)
        if key in seen:
            return
        seen.add(key)
        sc = sum(cand_score[c.id] for c in selected)
        found.append((sc, tuple(c.id for c in selected), list(selected)))

    def over_limit() -> bool:
        stats.elapsed = time.monotonic() - start
        if stats.nodes >= node_limit or stats.elapsed >= time_limit:
            stats.hit_limit = True
            return True
        return False

    def dfs(start_i: int, selected: list[Course], credits: float,
            used: set[TimeSlot]) -> None:
        if stats.hit_limit or over_limit():
            return
        stats.nodes += 1

        # 目前若已在學分範圍內 → 記錄為一組可行課表
        total = fixed_credits + credits
        if prefs.min_credits <= total <= prefs.max_credits:
            record(selected)

        if total >= prefs.max_credits:
            return  # 再加必超過上限
        if max_added is not None and len(selected) >= max_added:
            return  # 已達最多加入門數

        for i in range(start_i, len(cands)):
            if stats.hit_limit or over_limit():
                return
            # 剪枝：即使把 i..end 全加上也達不到下限，放棄
            if fixed_credits + credits + suffix_credits[i] < prefs.min_credits:
                break
            c = cands[i]
            if credits + c.credits > prefs.max_credits:
                continue
            if cand_slots[c.id] & used:                 # 與已選衝堂
                continue
            selected.append(c)
            dfs(i + 1, selected, credits + c.credits, used | cand_slots[c.id])
            selected.pop()

    dfs(0, [], 0.0, set())
    stats.elapsed = time.monotonic() - start

    # 排序（分數高→低；同分以課號組合穩定排序）取前 K，去重
    found.sort(key=lambda t: (-t[0], t[1]))
    schedules: list[Schedule] = []
    for sc, _ids, selected in found[:max_schedules]:
        reasons = {c.id: _default_reason(c, prefs) for c in selected}
        schedules.append(Schedule(
            fixed=fixed,
            added=selected,
            total_credits=fixed_credits + sum(c.credits for c in selected),
            score=sc,
            reasons=reasons,
        ))
    return schedules, stats


def _default_reason(course: Course, prefs: Preferences) -> str:
    """階段 2 的占位推薦理由（階段 4 由語言模型依課程資料強化）。"""
    bits = []
    if ge_domain_matches(course.ge_domain, prefs.ge_domains):
        bits.append(f"符合通識領域「{course.ge_domain}」")
    if prefs.languages and course.language in prefs.languages:
        bits.append(f"符合語言偏好「{course.language}」")
    bits.append("可填入你的空堂且不衝堂")
    return "；".join(bits)
