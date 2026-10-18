# -*- coding: utf-8 -*-
"""資料補完：回填真實課程的 language、ge_domain、syllabus_text。

抓取階段（fetch_courses.py）只取基礎與總表欄位，三個需「加工／逐門大綱」的欄位留白：
- language（語言課教的語種）：由課名推導（離線、確定性，覆蓋約 99% 語言課）。
- ge_domain（通識領域）：逐門抓大綱「課程領域」欄（需連網）。
- syllabus_text（大綱摘要）：逐門抓大綱「授課內容」欄（需連網），供語意搜尋。

大綱為「一門一請求」，成本高，因此：
- 預設只補「通識（課號 GS 或系所含通識）＋語言課」這些最需要的課。
- 遵守請求間隔 ≥1.2 秒、可識別 User-Agent，原始回應快取於 data/raw/。
- --max-requests 限制本次最多網路請求次數（預設 250），避免費用／負載失控。
- 可續跑：已有 ge_domain 的課跳過（--refresh 可重做）。

用法：
    python scripts/enrich_courses.py --lang            # 只做語言（離線，安全）
    python scripts/enrich_courses.py --outline         # 只抓大綱補通識/大綱摘要（連網）
    python scripts/enrich_courses.py                   # 兩者都做
    python scripts/enrich_courses.py --targets all     # 大綱補「全校」而非只通識/語言

沿用參考實作 NCU-Course-Finder-DataFetcher-v2（作者 Raku Zeta, MIT）的 API 請求方式。
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from planner.syllabus import parse_outline  # noqa: E402


def _force_utf8_stdout() -> None:
    """命令列執行時把 stdout 轉為 UTF-8（import 時不執行，避免干擾 pytest 擷取）。"""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


RAW_DIR = PROJECT_ROOT / "data" / "raw"
COURSES_PATH = PROJECT_ROOT / "data" / "courses.json"
USER_AGENT = ("NCU-Course-Planner-Prototype/0.1 "
              "(student project; contact chenxiwu226@gmail.com)")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW"}
MIN_INTERVAL = 1.2

# 課名語種關鍵字 → 標準語種名（與 sample_courses.json / preferences 一致）
_LANG_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("英語", ("英文", "英語")),
    ("日語", ("日文", "日語")),
    ("德語", ("德文", "德語")),
    ("法語", ("法文", "法語")),
    ("西班牙語", ("西班牙文", "西班牙語")),
    ("韓語", ("韓文", "韓語")),
    ("泰語", ("泰文", "泰語")),
    ("越南語", ("越南語", "越南文")),
    ("華語", ("華語", "中文")),
    ("台語", ("台語", "臺語")),
    ("客語", ("客語", "客家")),
]


def derive_language(name: str) -> str | None:
    """由課名推導語言課在教的語種；無法判斷回 None。"""
    if not name:
        return None
    for std, kws in _LANG_RULES:
        if any(k in name for k in kws):
            return std
    return None


def _is_ge_or_lang(c: dict) -> bool:
    code = (c.get("code") or "").upper()
    dept = c.get("dept") or ""
    return code.startswith("GS") or ("通識" in dept) or bool(c.get("is_language"))


# ---------------------------------------------------------------- 網路
_last_ts = 0.0


def fetch(url: str, cache_key: str, refetch: bool) -> tuple[bytes, bool]:
    """回傳 (內容, 是否真的連網)。有快取且非 refetch 時直接讀檔、不連網。"""
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


# ---------------------------------------------------------------- 各階段
def phase_language(courses: list[dict]) -> int:
    """離線：由課名回填語言課的 language。回傳新填入筆數。"""
    filled = 0
    for c in courses:
        if not c.get("is_language") or c.get("language"):
            continue
        lang = derive_language(c.get("name", ""))
        if lang:
            c["language"] = lang
            filled += 1
    return filled


def phase_outline(courses: list[dict], targets: str, max_requests: int,
                  refresh: bool, refetch: bool) -> tuple[int, int]:
    """連網：逐門抓大綱，回填 ge_domain 與 syllabus_text。

    refresh：連已回填的課也重做（用快取 HTML 重新解析，不一定重打網路）。
    refetch：忽略 data/raw/ 的 HTML 快取，重新向伺服器下載。
    回傳 (網路請求次數, 成功解析筆數)。
    """
    if targets == "ge-lang":
        pool = [c for c in courses if _is_ge_or_lang(c)]
    else:
        pool = list(courses)
    pending = [c for c in pool
               if c.get("syllabus_url") and (refresh or not c.get("ge_domain"))]

    print(f"  大綱補完對象（{targets}）：{len(pool)} 門；待處理：{len(pending)} 門")
    print(f"  網路請求上限 --max-requests={max_requests}"
          f"（讀快取不計入；--refetch 可強制重新下載）")

    net_done = 0      # 真正連網的次數（受 max_requests 限制）
    processed = 0     # 已解析處理的門數（含讀快取）
    parsed = 0
    for c in pending:
        if net_done >= max_requests:
            print(f"  已達網路請求上限 {max_requests}，剩餘 {len(pending) - processed} 門留待下次。")
            break
        try:
            html, hit_net = fetch(c["syllabus_url"], f"outline_{c['id']}.html", refetch)
        except Exception as e:  # noqa: BLE001  單門失敗不中斷
            print(f"    ! {c['id']} {c.get('name','')} 抓取失敗：{e}")
            net_done += 1
            processed += 1
            continue
        net_done += int(hit_net)
        processed += 1
        info = parse_outline(html.decode("utf-8", "replace"))
        if info["ge_domain"]:
            c["ge_domain"] = info["ge_domain"]
        if info["syllabus_text"]:
            c["syllabus_text"] = info["syllabus_text"]
        if info["ge_domain"] or info["syllabus_text"]:
            parsed += 1
        if processed % 25 == 0:
            print(f"    進度 {processed}/{len(pending)} …（連網 {net_done}）")
    return net_done, parsed


def main() -> None:
    ap = argparse.ArgumentParser(description="資料補完：language / ge_domain / syllabus_text")
    ap.add_argument("--lang", action="store_true", help="只做語言回填（離線）")
    ap.add_argument("--outline", action="store_true", help="只做大綱回填（連網）")
    ap.add_argument("--targets", choices=["ge-lang", "all"], default="ge-lang",
                    help="大綱補完範圍：ge-lang=只通識/語言（預設）；all=全校")
    ap.add_argument("--max-requests", type=int, default=250,
                    help="本次最多網路請求次數（預設 250）")
    ap.add_argument("--refresh", action="store_true",
                    help="連已回填的課也重做（優先用快取 HTML 重新解析，不一定重打網路）")
    ap.add_argument("--refetch", action="store_true",
                    help="忽略 data/raw/ 的 HTML 快取，強制重新向伺服器下載")
    ap.add_argument("--courses", type=Path, default=COURSES_PATH, help="courses.json 路徑")
    args = ap.parse_args()
    _force_utf8_stdout()

    # 未指定則兩者都做
    do_lang = args.lang or not (args.lang or args.outline)
    do_outline = args.outline or not (args.lang or args.outline)

    if not args.courses.exists():
        print(f"找不到 {args.courses}；請先執行 scripts/fetch_courses.py 抓取真實資料。")
        return
    payload = json.loads(args.courses.read_text(encoding="utf-8"))
    courses = payload["courses"]
    print(f"載入 {len(courses)} 門（{args.courses.relative_to(PROJECT_ROOT)}）")

    if do_lang:
        print("\n[語言] 由課名回填語言課語種（離線）…")
        n = phase_language(courses)
        total_lang = sum(1 for c in courses if c.get("language"))
        print(f"  新填 {n} 門；目前有語種的課共 {total_lang} 門")

    if do_outline:
        print("\n[大綱] 逐門抓大綱回填 ge_domain / syllabus_text（連網）…")
        req, parsed = phase_outline(courses, args.targets, args.max_requests,
                                    args.refresh, args.refetch)
        print(f"  本次連網 {req} 次，成功解析 {parsed} 門")

    args.courses.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")
    ge = sum(1 for c in courses if c.get("ge_domain"))
    syl = sum(1 for c in courses if c.get("syllabus_text"))
    lang = sum(1 for c in courses if c.get("language"))
    print(f"\n完成並寫回 {args.courses.relative_to(PROJECT_ROOT)}")
    print(f"  目前完整度：language {lang} 門、ge_domain {ge} 門、syllabus_text {syl} 門")


if __name__ == "__main__":
    main()
