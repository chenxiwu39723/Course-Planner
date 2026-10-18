# -*- coding: utf-8 -*-
"""課程資料載入（階段 1）。

由環境變數 DATA_SOURCE 決定載入哪份資料：
    DATA_SOURCE=live   （預設）→ data/courses.json（fetch_courses.py 產出的真實資料）
    DATA_SOURCE=sample          → data/sample_courses.json（虛構，供無資料時開發介面）

live 檔不存在或解析失敗時，自動退回 sample，不讓程式/網頁當掉；因此沒有
courses.json 的環境仍可運作（會顯示範例資料警告）。
"""
from __future__ import annotations

import datetime
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
    source = (source or os.environ.get("DATA_SOURCE", "live")).lower()

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


def _semester_label(sem: str | None) -> str | None:
    """把學年期代碼轉成可讀標籤，如 '1151' → '115 學年度第 1 學期'。"""
    if not sem:
        return None
    s = str(sem)
    if len(s) == 4 and s.isdigit():
        return f"{s[:3]} 學年度第 {s[3]} 學期"
    return s


def load_live_meta() -> dict:
    """回傳 live 資料檔的頂層中繼資料（semester / count / source / 更新日期）。

    供網頁標示資料是哪個學年期、何時抓取。檔案不存在或非 dict 格式時回傳空 dict。
    """
    try:
        if LIVE_PATH.exists():
            raw = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                updated = datetime.date.fromtimestamp(
                    LIVE_PATH.stat().st_mtime).isoformat()
                sem = raw.get("semester")
                return {
                    "semester": sem,
                    "semester_label": _semester_label(sem),
                    "count": raw.get("count"),
                    "source": raw.get("source"),
                    "updated": updated,
                }
    except Exception as e:  # noqa: BLE001
        print(f"[data_loader] 讀取 live 中繼資料失敗（{e}）。")
    return {}
