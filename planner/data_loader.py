# -*- coding: utf-8 -*-
"""課程資料載入（階段 1）。

由環境變數 DATA_SOURCE 決定載入哪份資料：
    DATA_SOURCE=sample （預設）→ data/sample_courses.json（虛構，無網路也能測）
    DATA_SOURCE=live            → data/courses.json（fetch_courses.py 產出）

依 CLAUDE.md：live 檔不存在或解析失敗時，自動退回 sample，不讓程式/網頁當掉。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Course

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LIVE_PATH = DATA_DIR / "courses.json"
SAMPLE_PATH = DATA_DIR / "sample_courses.json"


def _read(path: Path) -> list[Course]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw["courses"] if isinstance(raw, dict) else raw
    return [Course.from_dict(r) for r in rows]


def load_courses(source: str | None = None) -> tuple[list[Course], str]:
    """載入課程清單。

    回傳 (courses, actual_source)。actual_source 為 'live' 或 'sample'，
    方便網頁標示是否為範例資料。
    """
    source = (source or os.environ.get("DATA_SOURCE", "sample")).lower()

    if source == "live":
        try:
            if LIVE_PATH.exists():
                return _read(LIVE_PATH), "live"
            print(f"[data_loader] 找不到 {LIVE_PATH.name}，退回範例資料。")
        except Exception as e:  # noqa: BLE001
            print(f"[data_loader] 讀取 {LIVE_PATH.name} 失敗（{e}），退回範例資料。")

    return _read(SAMPLE_PATH), "sample"


def is_sample_source(actual_source: str) -> bool:
    return actual_source == "sample"
