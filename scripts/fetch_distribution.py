# -*- coding: utf-8 -*-
"""階段 3 增強：離線批次抓取「分發條件頁」，解析成結構化限修條件。

分發條件頁（?limit=<流水號>）比純備註可靠（資料可行性報告第五節）。本腳本：
- 由流水號直接組出網址，逐門抓取、原始回應快取於 data/raw/limit_<流水號>.html。
- 解析成 EligibilityCondition（planner/distribution.py），寫入
  data/distribution_cache.json；網頁使用時只讀快取、不即時呼叫。

遵守請求間隔 ≥1.2 秒、可識別 User-Agent；--max-requests 限制本次連網次數（預設 250）；
可續跑（已在快取者跳過，--refetch 可重抓）。

用法：
    python scripts/fetch_distribution.py --targets ge-lang           # 通識/語言（預設）
    python scripts/fetch_distribution.py --targets notes --max-requests 500
    python scripts/fetch_distribution.py --targets all               # 全校（慢）

沿用參考實作 NCU-Course-Finder-DataFetcher-v2（作者 Raku Zeta, MIT）的 API 請求方式。
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from planner.data_loader import load_courses  # noqa: E402
from planner.distribution import parse_distribution, raw_text, to_condition  # noqa: E402


def _force_utf8_stdout() -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


RAW_DIR = PROJECT_ROOT / "data" / "raw"
CACHE_PATH = PROJECT_ROOT / "data" / "distribution_cache.json"
BASE = "https://cis.ncu.edu.tw/Course/main"
SEMESTER = "1151"
USER_AGENT = ("NCU-Course-Planner-Prototype/0.1 "
              "(student project; contact chenxiwu226@gmail.com)")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW"}
MIN_INTERVAL = 1.2
_last_ts = 0.0


def _limit_url(serial: str) -> str:
    return f"{BASE}/query/byKeywords?limit={int(serial)}&semester={SEMESTER}"


def fetch(url: str, cache_key: str, refetch: bool) -> tuple[bytes, bool]:
    """回傳 (內容, 是否真的連網)。有快取且非 refetch 時直接讀檔。"""
    global _last_ts
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = RAW_DIR / cache_key
    if cache.exists() and not refetch:
        return cache.read_bytes(), False
    wait = MIN_INTERVAL - (time.time() - _last_ts)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers=HEADERS, timeout=30)
    _last_ts = time.time()
    resp.raise_for_status()
    cache.write_bytes(resp.content)
    return resp.content, True


def _select_targets(courses, targets: str):
    if targets == "all":
        return courses
    if targets == "notes":
        return [c for c in courses if (c.notes or "").strip()]
    # ge-lang
    out = []
    for c in courses:
        code = (c.code or "").upper()
        if code.startswith("GS") or "通識" in (c.dept or "") or c.is_language:
            out.append(c)
    return out


def _load_existing(refresh: bool) -> dict:
    if CACHE_PATH.exists() and not refresh:
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        return raw.get("conditions", {}) if isinstance(raw, dict) else {}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="階段 3 增強：抓取並解析分發條件")
    ap.add_argument("--source", choices=["live", "sample"], default=None)
    ap.add_argument("--targets", choices=["ge-lang", "notes", "all"], default="ge-lang")
    ap.add_argument("--max-requests", type=int, default=250,
                    help="本次最多連網次數（讀快取不計入；預設 250）")
    ap.add_argument("--refresh", action="store_true", help="忽略既有解析結果，全部重做")
    ap.add_argument("--refetch", action="store_true", help="忽略 HTML 快取，重新下載")
    args = ap.parse_args()
    _force_utf8_stdout()

    courses, src = load_courses(args.source)
    pool = _select_targets(courses, args.targets)
    existing = _load_existing(args.refresh)
    pending = [c for c in pool if c.id not in existing]

    print(f"資料來源：{src}；目標（{args.targets}）：{len(pool)} 門；"
          f"已快取：{len(existing)}；待處理：{len(pending)} 門")
    print(f"連網上限 --max-requests={args.max_requests}（讀快取不計入）")

    conditions = dict(existing)
    net_done = 0
    processed = 0
    with_cond = 0
    for c in pending:
        if net_done >= args.max_requests:
            print(f"已達連網上限 {args.max_requests}，剩餘 {len(pending) - processed} 門留待下次。")
            break
        try:
            html, hit = fetch(_limit_url(c.id), f"limit_{int(c.id)}.html", args.refetch)
        except Exception as e:  # noqa: BLE001
            print(f"  ! {c.id} {c.name} 抓取失敗：{e}")
            net_done += 1
            processed += 1
            continue
        net_done += int(hit)
        processed += 1
        rows = parse_distribution(html.decode("utf-8", "replace"))
        cond = to_condition(rows)
        conditions[c.id] = {
            "condition": cond.to_dict(),
            "raw": raw_text(rows),
            "rows": rows,
        }
        if rows:
            with_cond += 1
        if processed % 25 == 0:
            print(f"  進度 {processed}/{len(pending)} …（連網 {net_done}）")

    CACHE_PATH.write_text(json.dumps(
        {"source": src, "semester": SEMESTER, "count": len(conditions),
         "conditions": conditions}, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n完成：本次連網 {net_done} 次、處理 {processed} 門；"
          f"其中有分發條件 {with_cond} 門。")
    print(f"快取共 {len(conditions)} 門 → {CACHE_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
