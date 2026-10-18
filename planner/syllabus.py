# -*- coding: utf-8 -*-
"""課程大綱解析（資料補完用）。

從課程大綱 HTML 取出：
- 課程領域（ge_domain）：大綱「課程領域」欄，如「人文與思想領域」、語言課為「第二外語」
- 大綱摘要（syllabus_text）：大綱「授課內容」欄純文字（供語意搜尋語料）
- 開課單位（open_unit）：備用

大綱頁為乾淨的「標籤格／值格」表格；以「標籤格文字完全相等」定位後取同列下一格，
避免誤抓到包含該字樣的其他欄位。純解析、不涉網路（網路請求在 scripts/enrich_courses.py）。
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# 大綱「課程領域」可能出現的值；語言課為「第二外語」
_MAX_SYLLABUS_TEXT = 2000


def _value_after(soup: BeautifulSoup, label: str) -> str | None:
    """找出「標籤格文字 == label」的儲存格，回傳同列下一個儲存格的文字。"""
    for el in soup.find_all(string=re.compile(re.escape(label))):
        cell = el.find_parent(["td", "th"])
        if cell is None:
            continue
        if cell.get_text(" ", strip=True) != label:
            continue
        nxt = cell.find_next_sibling(["td", "th"])
        if nxt is not None:
            val = nxt.get_text(" ", strip=True)
            if val:
                return val
    return None


def parse_outline(html: str) -> dict:
    """解析大綱 HTML，回傳 {ge_domain, syllabus_text, open_unit}（缺項為 None）。"""
    soup = BeautifulSoup(html, "lxml")
    ge_domain = _value_after(soup, "課程領域")
    open_unit = _value_after(soup, "開課單位")
    content = _value_after(soup, "授課內容")
    if ge_domain:
        # 多領域課程的值格含換行／tab；領域名無內部空白，全部壓掉即可，如「文化與社會、第二外語」
        ge_domain = re.sub(r"\s+", "", ge_domain)
    if open_unit:
        open_unit = re.sub(r"\s+", "", open_unit)
    if content:
        content = re.sub(r"\s+", " ", content).strip()[:_MAX_SYLLABUS_TEXT]
    return {
        "ge_domain": ge_domain or None,
        "syllabus_text": content or None,
        "open_unit": open_unit or None,
    }
