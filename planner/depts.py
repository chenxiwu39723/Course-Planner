# -*- coding: utf-8 -*-
"""學生可選的系所清單。

課程資料的 dept 是「開課單位」：含體育室、語言中心等行政單位、學院本身，
跨系合開時以「、」串接，也可能帶「(已撤銷)」字樣，不能直接當成學生系所。
這裡把開課單位拆開、清理後分成學士班與研究所兩組。
（畢業規則的 PROGRAM_REGISTRY.json 系名都已涵蓋在課程資料中，且有錯字，故不合併。）
"""
from __future__ import annotations

import re

from planner.models import Course

_REVOKED = re.compile(r"^[（(]已撤銷[)）]")
_TRAILING_NOTE = re.compile(r"[（(][^()（）]*[)）]$")   # 例：(台灣聯合大學系統)
_UNDERGRAD_SUFFIX = ("學系", "系", "學士班")
_GRADUATE_SUFFIX = ("碩士班", "博士班", "在職專班", "學位學程", "研究所")


def split_units(dept: str) -> list[str]:
    """把開課單位字串拆成個別單位名稱，並移除「(已撤銷)」前綴。"""
    names = []
    for part in (dept or "").split("、"):
        name = _REVOKED.sub("", part.strip()).strip()
        if name and name not in names:
            names.append(name)
    return names


def classify(name: str) -> str | None:
    """回傳 "undergrad"、"graduate"；行政單位、學院本身、學程聯盟等回傳 None。"""
    core = _TRAILING_NOTE.sub("", name).strip()
    if core.endswith(_GRADUATE_SUFFIX):
        return "graduate"
    if core.endswith(_UNDERGRAD_SUFFIX):
        return "undergrad"
    return None


def student_depts(courses: list[Course]) -> dict[str, list[str]]:
    """回傳 {"undergrad": [...], "graduate": [...]}，各自排序、不重複。"""
    groups: dict[str, set[str]] = {"undergrad": set(), "graduate": set()}
    for c in courses:
        for name in split_units(c.dept):
            kind = classify(name)
            if kind:
                groups[kind].add(name)
    return {k: sorted(v) for k, v in groups.items()}
