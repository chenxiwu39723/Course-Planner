# -*- coding: utf-8 -*-
"""分發條件（限修）解析（階段 3 增強）。

課程查詢系統除了自由文字備註外，另有「分發條件頁」（?limit=<流水號>），內容為
半結構化的表格：優先順序 | 相關條件限制說明，如：
    1 | 學制:限學士班。
    2 | 身份:限申請校學士。
    3 | 系所:限資訊工程學系。

比純備註可靠（資料可行性報告第五節建議優先解析此頁，備註作補充）。本模組：
- parse_distribution(html)：取出 [(優先序, 類別, 說明)] 原始列。
- to_condition(rows)：轉成與 eligibility 相同的 EligibilityCondition（結構化欄位）。

解析沿用 eligibility.keyword_parse 的確定性關鍵字規則（年級/系所/先修/其他），
不呼叫語言模型；因來源為結構化表格，信心度設為 high。
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .eligibility import EligibilityCondition, keyword_parse

# 類別與說明的分隔符號（全形／半形冒號）
_SEP_RE = re.compile(r"[:：]")


def parse_distribution(html: str) -> list[dict]:
    """解析分發條件頁，回傳 [{priority, category, description, raw}]。

    只取「第一格為純數字、共兩格」的資料列，忽略表頭與合併雜訊列。
    """
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if len(cells) != 2:
            continue
        left = cells[0].get_text(" ", strip=True)
        right = cells[1].get_text(" ", strip=True)
        if not left.isdigit() or not right:
            continue
        priority = int(left)
        text = re.sub(r"\s+", "", right).rstrip("。")
        key = (priority, text)
        if key in seen:                       # 頁面有時重覆同一列，去重
            continue
        seen.add(key)
        m = _SEP_RE.split(text, 1)
        if len(m) == 2:
            category, desc = m[0].strip(), m[1].strip()
        else:
            category, desc = "", text
        rows.append({"priority": priority, "category": category,
                     "description": desc, "raw": text})
    rows.sort(key=lambda r: r["priority"])
    return rows


def to_condition(rows: list[dict]) -> EligibilityCondition:
    """把分發條件列轉成 EligibilityCondition（結構化、信心度 high）。"""
    if not rows:
        return EligibilityCondition(confidence="high")  # 有頁但無條件 = 無限制
    # 合併所有說明文字，交給確定性關鍵字規則萃取年級/系所/先修/其他
    blob = "。".join(r["raw"] for r in rows)
    cond = keyword_parse(blob)
    # 確保每一列都有被記錄（沒被歸入結構化欄位者補進 other_conditions）
    for r in rows:
        txt = r["raw"]
        covered = (
            any(d in txt or txt in d for d in cond.allowed_depts)
            or any(p in txt for p in cond.prerequisites)
            or any(o in txt or txt in o for o in cond.other_conditions)
            or (cond.min_grade is not None and "年級" in txt)
        )
        if not covered:
            cond.other_conditions.append(txt)
    cond.confidence = "high"                  # 結構化來源
    return cond


def raw_text(rows: list[dict]) -> str:
    """供網頁顯示的原始分發條件文字。"""
    return "；".join(f"{r['priority']}. {r['raw']}" for r in rows)
