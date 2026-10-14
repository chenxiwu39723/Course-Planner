# -*- coding: utf-8 -*-
"""統一的課程資料模型（階段 1）。

使用標準庫 dataclass（零相依、方便測試）。欄位對應 CLAUDE.md 規劃：
    id, code, name, teacher, dept, credits, category, ge_domain,
    is_language, language, times[(day, period)], classroom, capacity,
    notes, syllabus_url, syllabus_text

時間以 (day, period) 表示：
    day    : int，星期碼 0=日,1=一,...,6=六（與課程查詢系統 week 下拉一致）
    period : str，節次碼，之一：1 2 3 4 Z 5 6 7 8 9 A B C D E F
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# (day:int, period:str)
TimeSlot = tuple[int, str]


@dataclass
class Course:
    """一門課（一個開課班次 = 一個流水號）。"""

    id: str                      # 流水號（全校唯一 5 碼字串）
    code: str                    # 課號-班次，如 "GS2064-*"、"LN0025-A"
    name: str                    # 中文課名
    teacher: list[str]           # 授課教師（可能多位）
    dept: str                    # 開課系所／單位（中文名）
    credits: float               # 學分
    times: list[TimeSlot]        # 上課時間 (day, period) 清單

    category: str | None = None          # 選修別：選修／必修
    ge_domain: str | None = None         # 通識領域（精確值來自大綱）
    is_language: bool = False             # 是否為語言／第二外語課
    language: str | None = None           # 授課語言
    classroom: str | None = None          # 教室（含大樓名）
    capacity: int | None = None           # 人數限制（0/未填 → None）
    notes: str = ""                       # 備註（自由文字，含限修說明）
    syllabus_url: str | None = None       # 課程大綱連結
    syllabus_text: str | None = None      # 課程大綱純文字（離線批次補抓）

    # 額外欄位
    name_en: str | None = None            # 英文課名
    admit_cnt: int | None = None          # 已核定人數
    wait_cnt: int | None = None           # 候補人數
    is_sample: bool = False               # True = 虛構範例資料

    def to_dict(self) -> dict[str, Any]:
        """轉為可 JSON 序列化的 dict（times 的 tuple 轉為 list）。"""
        d = asdict(self)
        d["times"] = [[day, period] for day, period in self.times]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Course":
        """由 dict 還原（times 的 list 轉回 tuple）。容忍未知欄位。"""
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        data = {k: v for k, v in d.items() if k in known}
        data["times"] = [(int(day), str(period)) for day, period in data.get("times", [])]
        if "teacher" in data and isinstance(data["teacher"], str):
            data["teacher"] = [data["teacher"]]
        return cls(**data)
