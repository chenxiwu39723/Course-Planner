# -*- coding: utf-8 -*-
"""階段 0：資料可行性檢查腳本。

對中大課程查詢系統（https://cis.ncu.edu.tw/）實際請求本學期（115-1，代碼 1151）
資料一次，檢查 API 是否仍可用、各欄位是否存在、課程大綱是否能在未登入下取得、
通識領域與授課語言的識別方式，以及節次代碼清單。

規範：
- 只抓不需登入的公開資料，不涉及任何學生帳號、成績、個人選課紀錄。
- 每次請求間隔至少 1 秒，設定可識別的 User-Agent。
- 原始回應快取在 data/raw/，重複執行時直接讀快取，避免重複請求（--refresh 可強制重抓）。

可獨立執行：python scripts/check_data_source.py
路徑以 pathlib 相對於專案根目錄。
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from lxml import etree

# Windows 終端機預設 cp950，統一用 UTF-8 輸出避免中文亂碼
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"

BASE = "https://cis.ncu.edu.tw/Course/main"
SEMESTER = "1151"  # 115 學年第 1 學期（2026 Fall），由 byKeywords 的學期下拉確認
USER_AGENT = (
    "NCU-Course-Planner-Prototype/0.1 "
    "(student project; contact chenxiwu226@gmail.com)"
)
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW"}
MIN_INTERVAL = 1.2  # 秒

_last_request_ts = 0.0


def fetch(url: str, params: dict | None = None, *, cache_key: str, refresh: bool) -> bytes:
    """GET 一個 URL，回應快取到 data/raw/<cache_key>。遵守最小請求間隔。"""
    global _last_request_ts
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = RAW_DIR / cache_key
    if cache_path.exists() and not refresh:
        print(f"  [cache] {cache_key}")
        return cache_path.read_bytes()

    wait = MIN_INTERVAL - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    print(f"  [GET]   {url}  params={params}")
    resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
    _last_request_ts = time.time()
    print(f"          -> {resp.status_code}, {len(resp.content)} bytes, "
          f"{resp.headers.get('Content-Type')}")
    resp.raise_for_status()
    cache_path.write_bytes(resp.content)
    return resp.content


def section(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


# --------------------------------------------------------------------------
# 檢查 1：系所清單（byUnion）
# --------------------------------------------------------------------------
def check_departments(refresh: bool) -> list[dict]:
    section("檢查 1｜系所清單 byUnion（判斷 API 可用 + 通識/語言識別）")
    html = fetch(f"{BASE}/query/byUnion", cache_key="byUnion.html", refresh=refresh)
    soup = BeautifulSoup(html, "lxml")
    depts = []
    for a in soup.select('a[href*="byUnion?dept="]'):
        name = a.get_text(strip=True)
        dept_id = a.get("href").split("dept=")[-1]
        m = re.search(r"\((\d+)\)$", name)
        count = int(m.group(1)) if m else None
        clean = re.sub(r"\(\d+\)$", "", name)
        depts.append({"dept_id": dept_id, "name": clean, "count": count})
    print(f"  取得系所數：{len(depts)}")
    ge_lang = [d for d in depts if any(k in d["name"]
               for k in ("通識", "語言", "核心通識"))]
    print("  通識／語言相關單位（可用系所歸類識別）：")
    for d in ge_lang:
        print(f"    - {d['dept_id']:14} {d['name']}（{d['count']} 門）")
    return depts


# --------------------------------------------------------------------------
# 檢查 2：course.xml 的欄位（base 資料）
# --------------------------------------------------------------------------
def check_course_xml(depts: list[dict], refresh: bool) -> dict:
    section("檢查 2｜course.xml 基礎欄位（時間/學分/教師/人數）")
    # 挑幾個代表性系所：資工、語言中心、通識教育中心
    wanted = ["deptI1I5002I0", "deptI0I10I0", "deptI0I9I0"]
    sample_ids = [d["dept_id"] for d in depts if d["dept_id"] in wanted] or \
                 [depts[0]["dept_id"]]
    all_attrs: set[str] = set()
    first_samples = []
    for dept_id in sample_ids:
        xml = fetch(f"{BASE}/support/course.xml", params={"id": dept_id},
                    cache_key=f"course_{dept_id}.xml", refresh=refresh)
        root = etree.fromstring(xml)
        courses = root.findall(".//Course")
        print(f"  {dept_id}: <Course> {len(courses)} 門")
        for c in courses:
            all_attrs.update(c.attrib.keys())
        if courses and not first_samples:
            for c in courses[:3]:
                first_samples.append(dict(c.attrib))
    print(f"\n  course.xml 實際出現的屬性：{sorted(all_attrs)}")
    print("  範例（前 3 門）：")
    for s in first_samples:
        print("    ", {k: s.get(k) for k in
              ("SerialNo", "ClassNo", "Title", "Teacher", "credit",
               "ClassTime", "ClassTimeAlt", "limitCnt", "admitCnt", "waitCnt")})
    print("\n  判讀：course.xml 提供時間(ClassTime/ClassTimeAlt)、學分、教師、"
          "人數；不含教室、備註、類別、通識領域、大綱。")
    return {"attrs": sorted(all_attrs), "samples": first_samples}


# --------------------------------------------------------------------------
# 檢查 3：byKeywords 總表（備註/教室/類別/大綱連結）
# --------------------------------------------------------------------------
def check_bykeywords(refresh: bool) -> dict:
    section("檢查 3｜byKeywords 總表（備註=限修文字、教室、類別、大綱連結）")
    html = fetch(f"{BASE}/query/byKeywords",
                 params={"d-49489-p": 1, "query": "true"},
                 cache_key="byKeywords_p1.html", refresh=refresh)
    soup = BeautifulSoup(html, "lxml")
    heads = [th.get_text(" ", strip=True) for th in soup.select("#item thead th")]
    print(f"  表頭（{len(heads)} 欄）：{heads}")

    rows = soup.select("#item tbody tr")
    print(f"  第 1 頁列數：{len(rows)}")

    samples = []
    outline_urls, limit_urls = [], []
    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 10:
            continue
        serial, classno = (tds[0].get_text(" ", strip=True).split(" ", 1) + [""])[:2]
        name_parts = [t.strip() for t in tds[1].stripped_strings]
        notes = [p for p in name_parts if "[" in p or "。" in p]
        a_out = tds[9].find("a")
        a_lim = tds[8].find("a")
        om = re.search(r"'([^']+)'", a_out.get("onclick", "")) if a_out else None
        lm = re.search(r"'([^']+)'", a_lim.get("onclick", "")) if a_lim else None
        if om:
            outline_urls.append(om.group(1))
        if lm:
            limit_urls.append(lm.group(1))
        if len(samples) < 3:
            samples.append({
                "serial": serial, "classno": classno,
                "name": name_parts[:2], "notes": notes,
                "teacher": tds[2].get_text(strip=True),
                "credit": tds[3].get_text(strip=True),
                "time_room": tds[4].get_text(" ", strip=True),
                "type": tds[5].get_text(strip=True),
                "term": tds[6].get_text(strip=True),
                "limit_cnt": tds[7].get_text(strip=True),
                "outline_url": om.group(1) if om else None,
                "limit_url": lm.group(1) if lm else None,
            })

    print("  範例（前 3 門，含備註=自由文字）：")
    for s in samples:
        print("    " + "-" * 60)
        for k, v in s.items():
            print(f"      {k}: {v}")

    # 分頁總數
    last_page = None
    for a in soup.select(".pagelinks a"):
        m = re.search(r"d-49489-p=(\d+)", a.get("href", ""))
        if m:
            last_page = max(last_page or 0, int(m.group(1)))
    print(f"\n  分頁：偵測到的最大頁碼 ~= {last_page}（每頁 {len(rows)} 門）")
    print("  判讀：備註(td1)含限修/說明自由文字；教室(td4)；類別(td5=選/必修)；"
          "大綱與分發條件各有獨立可抓 URL。")
    return {"heads": heads, "samples": samples,
            "outline_urls": outline_urls, "limit_urls": limit_urls,
            "rows_per_page": len(rows), "last_page": last_page}


# --------------------------------------------------------------------------
# 檢查 4：課程大綱是否能未登入取得（抓 3 門測試）
# --------------------------------------------------------------------------
def check_outlines(bk: dict, refresh: bool) -> None:
    section("檢查 4｜課程大綱（未登入可否取得？抓 3 門測試）")
    urls = bk["outline_urls"][:3]
    if not urls:
        print("  找不到大綱連結，略過。")
        return
    for i, rel in enumerate(urls, 1):
        html = fetch(f"https://cis.ncu.edu.tw{rel}",
                     cache_key=f"outline_{i}.html", refresh=refresh)
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        need_login = any(k in text for k in ("請先登入", "登入系統", "login"))
        has_login_form = bool(soup.find("input", {"type": "password"}))
        print(f"\n  大綱 {i}: {rel}")
        print(f"    純文字長度：{len(text)}；疑似需登入：{need_login or has_login_form}")
        for kw in ("課程概述", "課程大綱", "教學目標", "教科書", "評量",
                   "先修", "領域", "授課語言", "英語"):
            if kw in text:
                idx = text.find(kw)
                print(f"    含『{kw}』：…{text[idx:idx+40]}…")
        print(f"    內文節錄：{text[:160]}")


# --------------------------------------------------------------------------
# 檢查 5：分發條件（限修）獨立頁
# --------------------------------------------------------------------------
def check_limits(bk: dict, refresh: bool) -> None:
    section("檢查 5｜分發條件／限修 獨立頁（抓 2 門測試）")
    urls = bk["limit_urls"][:2]
    if not urls:
        print("  找不到分發條件連結，略過。")
        return
    for i, rel in enumerate(urls, 1):
        html = fetch(f"https://cis.ncu.edu.tw{rel}",
                     cache_key=f"limit_{i}.html", refresh=refresh)
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        print(f"\n  分發條件 {i}: {rel}")
        print(f"    純文字長度：{len(text)}")
        print(f"    內文節錄：{text[:300]}")


# --------------------------------------------------------------------------
# 檢查 6：節次代碼清單 + 授課語言選項（取自 byKeywords 查詢表單）
# --------------------------------------------------------------------------
def check_codes(refresh: bool) -> None:
    section("檢查 6｜節次代碼清單、星期、授課語言（來自官方查詢表單）")
    html = fetch(f"{BASE}/query/byKeywords",
                 params={"d-49489-p": 1, "query": "true"},
                 cache_key="byKeywords_p1.html", refresh=refresh)
    soup = BeautifulSoup(html, "lxml")
    for name in ("week", "day", "crs_lang_no"):
        sel = soup.find("select", {"name": name})
        if not sel:
            print(f"  select[{name}] 不存在")
            continue
        opts = [(o.get("value"), o.get_text(strip=True))
                for o in sel.find_all("option") if o.get("value")]
        print(f"  {name}: {opts}")
    print("\n  判讀：節次代碼完整清單＝ 1 2 3 4 Z 5 6 7 8 9 A B C D E F（含中午 Z）。")
    print("  ⚠ 節次『對應時間』未出現在查詢頁，需另從官方使用手冊/課務組公告確認，"
          "不可自行假設。")


def main() -> None:
    ap = argparse.ArgumentParser(description="階段 0 資料可行性檢查")
    ap.add_argument("--refresh", action="store_true",
                    help="忽略快取，強制重新請求")
    args = ap.parse_args()

    print(f"學期：{SEMESTER}（115 學年第 1 學期）")
    print(f"快取目錄：{RAW_DIR}")

    depts = check_departments(args.refresh)
    check_course_xml(depts, args.refresh)
    bk = check_bykeywords(args.refresh)
    check_outlines(bk, args.refresh)
    check_limits(bk, args.refresh)
    check_codes(args.refresh)

    section("完成")
    print("原始回應已快取於 data/raw/。請見 docs/data_feasibility.md 的彙整結論。")


if __name__ == "__main__":
    main()
