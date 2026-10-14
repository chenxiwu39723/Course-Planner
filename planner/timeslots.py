# -*- coding: utf-8 -*-
"""節次與時間工具（階段 1）。

提供：
- 節次代碼清單與對應時間（來源見 docs/data_feasibility.md 第四節）
- 解析課程查詢系統的時間字串（機器碼 "27,28" 或可讀碼 "二78"）
- 兩門課是否衝堂
- 一週空堂格計算

所有時間判斷皆為確定性程式，不涉及語言模型（符合 CLAUDE.md 核心原則）。
"""
from __future__ import annotations

from typing import Iterable

from .models import Course, TimeSlot

# 節次代碼完整清單（取自查詢頁 day 下拉，順序即時間先後；Z 為中午節）
PERIOD_ORDER: list[str] = [
    "1", "2", "3", "4", "Z", "5", "6", "7", "8", "9",
    "A", "B", "C", "D", "E", "F",
]

# 節次 → (開始, 結束)。來源：中大課務組課程時間表使用說明。
# ⚠ E、F 為極少用的深夜節次，時間依規律推得，落地前應再向課務組最新說明確認。
PERIOD_TIMES: dict[str, tuple[str, str]] = {
    "1": ("08:00", "08:50"),
    "2": ("09:00", "09:50"),
    "3": ("10:00", "10:50"),
    "4": ("11:00", "11:50"),
    "Z": ("12:00", "12:50"),
    "5": ("13:00", "13:50"),
    "6": ("14:00", "14:50"),
    "7": ("15:00", "15:50"),
    "8": ("16:00", "16:50"),
    "9": ("17:00", "17:50"),
    "A": ("18:00", "18:50"),
    "B": ("19:00", "19:50"),
    "C": ("20:00", "20:50"),
    "D": ("21:00", "21:50"),
    "E": ("22:00", "22:50"),  # 待課務組確認
    "F": ("23:00", "23:50"),  # 待課務組確認
}

# 星期碼 → 名稱（與查詢頁 week 下拉一致：0=日 … 6=六）
WEEKDAY_NAMES: dict[int, str] = {
    0: "日", 1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六",
}
# 反查：中文星期 → 碼
_NAME_TO_WEEKDAY: dict[str, int] = {v: k for k, v in WEEKDAY_NAMES.items()}

# 工作日（週一到週五）與其節次集合，供空堂計算的預設範圍
DEFAULT_DAYS: list[int] = [1, 2, 3, 4, 5]
DEFAULT_PERIODS: list[str] = PERIOD_ORDER[:]

_VALID_PERIODS = set(PERIOD_ORDER)


def parse_machine_classtime(s: str) -> list[TimeSlot]:
    """解析機器碼時間字串，如 "27,28" → [(2,'7'), (2,'8')]。

    每個以逗號分隔的 token 固定 2 字元：第 1 字星期碼(0-6)、第 2 字節次碼。
    無法解析的 token 會被略過（容錯）。
    """
    slots: list[TimeSlot] = []
    for token in s.split(","):
        token = token.strip()
        if len(token) < 2:
            continue
        day_ch, period = token[0], token[1:]
        if not day_ch.isdigit():
            continue
        day = int(day_ch)
        if 0 <= day <= 6 and period in _VALID_PERIODS:
            slots.append((day, period))
    return slots


def parse_readable_classtime(s: str) -> list[TimeSlot]:
    """解析可讀碼時間字串，如 "二78"、"一234" → [(2,'7'),(2,'8')] 等。

    格式：一個中文星期字後，接一串節次碼字元（每字一節，含 Z、A–F）。
    可含多段（以空白或逗號分隔），如 "一2 三5"。
    """
    slots: list[TimeSlot] = []
    for seg in s.replace("，", " ").replace(",", " ").split():
        if not seg:
            continue
        day_ch, rest = seg[0], seg[1:]
        if day_ch not in _NAME_TO_WEEKDAY:
            continue
        day = _NAME_TO_WEEKDAY[day_ch]
        for period in rest:
            p = period.upper()
            if p in _VALID_PERIODS:
                slots.append((day, p))
    return slots


def slot_label(slot: TimeSlot) -> str:
    """把 (day, period) 轉成可讀字串，如 (2,'7') → "二7 (15:00-15:50)"。"""
    day, period = slot
    start, end = PERIOD_TIMES.get(period, ("?", "?"))
    return f"{WEEKDAY_NAMES.get(day, '?')}{period} ({start}-{end})"


def has_conflict(a: Course, b: Course) -> bool:
    """兩門課是否衝堂（有任一相同的 (day, period)）。"""
    return bool(set(a.times) & set(b.times))


def any_conflict(courses: Iterable[Course]) -> list[tuple[str, str]]:
    """回傳課程清單中所有互相衝堂的課號配對（空 list = 無衝堂）。"""
    items = list(courses)
    clashes: list[tuple[str, str]] = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if has_conflict(items[i], items[j]):
                clashes.append((items[i].id, items[j].id))
    return clashes


def occupied_slots(courses: Iterable[Course]) -> set[TimeSlot]:
    """課程清單佔用的所有 (day, period)。"""
    used: set[TimeSlot] = set()
    for c in courses:
        used.update(c.times)
    return used


def free_slots(
    fixed_courses: Iterable[Course],
    days: Iterable[int] | None = None,
    periods: Iterable[str] | None = None,
) -> set[TimeSlot]:
    """計算一週空堂格。

    days / periods 不給時，預設週一到週五 × 全部節次。
    回傳在範圍內、未被 fixed_courses 佔用的 (day, period) 集合。
    """
    days = list(days) if days is not None else DEFAULT_DAYS
    periods = list(periods) if periods is not None else DEFAULT_PERIODS
    used = occupied_slots(fixed_courses)
    grid = {(d, p) for d in days for p in periods}
    return grid - used


def fits_in_free_slots(course: Course, free: set[TimeSlot]) -> bool:
    """course 的所有上課時間是否都落在 free 空堂內（且本身有時間）。"""
    return bool(course.times) and set(course.times).issubset(free)
