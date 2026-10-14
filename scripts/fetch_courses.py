# -*- coding: utf-8 -*-
"""階段 1：抓取本學期（115-1）所有課程，正規化輸出 data/courses.json。

資料來源（詳見 docs/data_feasibility.md）：
- byUnion             → 系所清單
- support/course.xml  → 各系基礎欄位（時間機器碼、學分、教師、人數）
- query/byKeywords    → 備註、教室、選/必修、課名中英、大綱連結（逐頁）
兩來源以「流水號」join。

規範：請求間隔 ≥1.2 秒、可識別 User-Agent、原始回應快取於 data/raw/。
可獨立執行：python scripts/fetch_courses.py [--refresh] [--max-pages N]

沿用參考實作 NCU-Course-Finder-DataFetcher-v2（作者 Raku Zeta, MIT）的 API 與解析邏輯。
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
from bs4 import BeautifulSoup
from lxml import etree

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from planner.models import Course  # noqa: E402
from planner.timeslots import parse_machine_classtime, parse_readable_classtime  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUT_PATH = PROJECT_ROOT / "data" / "courses.json"
BASE = "https://cis.ncu.edu.tw/Course/main"
SEMESTER = "1151"
USER_AGENT = ("NCU-Course-Planner-Prototype/0.1 "
              "(student project; contact chenxiwu226@gmail.com)")
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW"}
MIN_INTERVAL = 1.2

_last_ts = 0.0


def fetch(url: str, params: dict | None, cache_key: str, refresh: bool) -> bytes:
    global _last_ts
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache = RAW_DIR / cache_key
    if cache.exists() and not refresh:
        return cache.read_bytes()
    wait = MIN_INTERVAL - (time.time() - _last_ts)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
    _last_ts = time.time()
    resp.raise_for_status()
    cache.write_bytes(resp.content)
    return resp.content


# ---------------------------------------------------------------- byUnion
def get_departments(refresh: bool) -> list[dict]:
    html = fetch(f"{BASE}/query/byUnion", None, "byUnion.html", refresh)
    soup = BeautifulSoup(html, "lxml")
    depts = []
    for a in soup.select('a[href*="byUnion?dept="]'):
        name = re.sub(r"\(\d+\)$", "", a.get_text(strip=True))
        dept_id = a.get("href").split("dept=")[-1]
        depts.append({"dept_id": dept_id, "name": name})
    return depts


# ------------------------------------------------------------- course.xml
def get_course_bases(depts: list[dict], refresh: bool) -> dict[str, dict]:
    """回傳 {流水號: base 欄位}；跨系開課則累積系名。"""
    bases: dict[str, dict] = {}
    for i, d in enumerate(depts, 1):
        try:
            xml = fetch(f"{BASE}/support/course.xml", {"id": d["dept_id"]},
                        f"course_{d['dept_id']}.xml", refresh)
            root = etree.fromstring(xml)
        except Exception as e:  # 單系失敗不中斷整體
            print(f"  ! {d['dept_id']} ({d['name']}) 失敗：{e}")
            continue
        n = 0
        for c in root.findall(".//Course"):
            a = c.attrib
            serial = str(a.get("SerialNo", "")).zfill(5)
            if not serial.strip("0") and serial != "00000":
                pass
            rec = bases.get(serial)
            if rec is None:
                rec = {
                    "id": serial,
                    "code": a.get("ClassNo", ""),
                    "name": a.get("Title", ""),
                    "teacher": [t for t in re.split(r",\s*", a.get("Teacher", "")) if t],
                    "depts": [],
                    "credits": _to_float(a.get("credit")),
                    "times": parse_machine_classtime(a.get("ClassTime", "")),
                    "capacity": _to_int_or_none(a.get("limitCnt")),
                    "admit_cnt": _to_int_or_none(a.get("admitCnt")),
                    "wait_cnt": _to_int_or_none(a.get("waitCnt")),
                }
                bases[serial] = rec
            if d["name"] not in rec["depts"]:
                rec["depts"].append(d["name"])
            n += 1
        print(f"  [{i:>2}/{len(depts)}] {d['dept_id']:14} {d['name']}：{n} 門")
    return bases


# -------------------------------------------------------------- byKeywords
def get_course_extras(refresh: bool, max_pages: int) -> dict[str, dict]:
    """逐頁抓 byKeywords，回傳 {流水號: extra 欄位}。"""
    extras: dict[str, dict] = {}
    for page in range(1, max_pages + 1):
        html = fetch(f"{BASE}/query/byKeywords",
                     {"d-49489-p": page, "query": "true"},
                     f"byKeywords_p{page}.html", refresh)
        soup = BeautifulSoup(html, "lxml")
        rows = soup.select("#item tbody tr")
        if not rows:
            break
        for tr in rows:
            tds = tr.find_all("td")
            if len(tds) < 10:
                continue
            serial = tds[0].get_text(" ", strip=True).split(" ", 1)[0].zfill(5)
            parts = [t.strip() for t in tds[1].stripped_strings]
            name = parts[0] if parts else ""
            name_en = parts[1] if len(parts) > 1 else None
            notes = " ".join(
                re.sub(r"\s+", " ", p).strip()
                for p in parts[2:] if p.strip()
            )
            time_room = tds[4].get_text(" ", strip=True)
            a_out = tds[9].find("a")
            out_url = None
            if a_out:
                m = re.search(r"'([^']+)'", a_out.get("onclick", ""))
                if m:
                    out_url = "https://cis.ncu.edu.tw" + m.group(1)
            extras[serial] = {
                "name": name,
                "name_en": name_en,
                "notes": notes,
                "category": tds[5].get_text(strip=True) or None,
                "classroom": _parse_classroom(time_room),
                "readable_time": _parse_readable_time(time_room),
                "capacity": _to_int_or_none(tds[7].get_text(strip=True)),
                "syllabus_url": out_url,
            }
        # 是否有下一頁
        last = soup.select_one(".pagelinks > :last-child")
        has_next = last is not None and last.name == "a"
        print(f"  byKeywords 第 {page} 頁：{len(rows)} 門（累積 {len(extras)}）")
        if not has_next:
            break
    return extras


def _parse_classroom(time_room: str) -> str | None:
    """從 "一234/TR-A001 (教研大樓-A001)" 取可讀教室 "教研大樓-A001"（可能多間）。"""
    rooms = re.findall(r"\(([^)]+)\)", time_room)
    rooms = [r.strip() for r in rooms if r.strip()]
    return "、".join(dict.fromkeys(rooms)) if rooms else None


def _parse_readable_time(time_room: str) -> str:
    """取出時間部分（去掉教室），如 "一234/TR-A001 (教研大樓-A001)" → "一234"。"""
    segs = []
    for seg in re.split(r"[,\s]+", time_room):
        t = seg.split("/", 1)[0].strip()
        if t and t[0] in "日一二三四五六":
            segs.append(t)
    return " ".join(segs)


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _to_int_or_none(v) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n != 0 else None


# -------------------------------------------------------------- 合併
def merge(bases: dict[str, dict], extras: dict[str, dict]) -> list[Course]:
    courses: list[Course] = []
    all_serials = set(bases) | set(extras)
    for serial in sorted(all_serials):
        b = bases.get(serial, {})
        e = extras.get(serial, {})

        # 時間：優先用 course.xml 機器碼；缺則用 byKeywords 可讀碼
        times = b.get("times") or []
        if not times and e.get("readable_time"):
            times = parse_readable_classtime(e["readable_time"])

        dept = "、".join(b.get("depts", [])) or "（未分類）"
        code = b.get("code") or ""
        name = b.get("name") or e.get("name") or ""
        is_lang = ("語言中心" in dept) or code.upper().startswith("LN")

        courses.append(Course(
            id=serial,
            code=code,
            name=name,
            teacher=b.get("teacher", []),
            dept=dept,
            credits=b.get("credits", 0.0),
            times=times,
            category=e.get("category"),
            ge_domain=None,                 # 精確領域需離線抓大綱（階段 3/4 補）
            is_language=is_lang,
            language=None,                  # 需大綱或 crs_lang_no 篩選（之後補）
            classroom=e.get("classroom"),
            capacity=b.get("capacity") or e.get("capacity"),
            notes=e.get("notes", ""),
            syllabus_url=e.get("syllabus_url"),
            syllabus_text=None,             # 離線批次補抓
            name_en=e.get("name_en"),
            admit_cnt=b.get("admit_cnt"),
            wait_cnt=b.get("wait_cnt"),
            is_sample=False,
        ))
    return courses


def main() -> None:
    ap = argparse.ArgumentParser(description="階段 1：抓取並正規化本學期課程")
    ap.add_argument("--refresh", action="store_true", help="忽略快取重新請求")
    ap.add_argument("--max-pages", type=int, default=300, help="byKeywords 最大頁數上限")
    args = ap.parse_args()

    print(f"學期 {SEMESTER}（115-1）｜輸出 {OUT_PATH.relative_to(PROJECT_ROOT)}")
    print("\n[1/3] 取得系所清單…")
    depts = get_departments(args.refresh)
    print(f"  共 {len(depts)} 個系所／單位")

    print("\n[2/3] 抓取各系 course.xml（基礎欄位）…")
    bases = get_course_bases(depts, args.refresh)
    print(f"  base 課程（去重後）：{len(bases)} 門")

    print("\n[3/3] 抓取 byKeywords（備註/教室/類別/大綱）…")
    extras = get_course_extras(args.refresh, args.max_pages)
    print(f"  extra 課程：{len(extras)} 門")

    courses = merge(bases, extras)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "semester": SEMESTER,
        "count": len(courses),
        "source": "cis.ncu.edu.tw",
        "courses": [c.to_dict() for c in courses],
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    with_time = sum(1 for c in courses if c.times)
    with_notes = sum(1 for c in courses if c.notes)
    print(f"\n完成：寫入 {len(courses)} 門 → {OUT_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  有上課時間：{with_time}；有備註：{with_notes}；"
          f"語言課：{sum(1 for c in courses if c.is_language)}")


if __name__ == "__main__":
    main()
