# -*- coding: utf-8 -*-
"""階段 3：離線批次解讀限修條件。

對「備註非空」的課程各呼叫一次語言模型，把結構化條件快取到
data/eligibility_cache.json。網頁使用時只讀快取、不即時呼叫（符合 CLAUDE.md）。

特性：
- 開始前印出預估呼叫次數。
- --max-calls 限制本次最多呼叫次數（預設 300），避免費用失控。
- 可續跑：已在快取中的課號會跳過（--force 可重做）。
- LLM_PROVIDER=mock 或未設定金鑰時，自動以關鍵字規則產生結果，不中斷。

可獨立執行：python scripts/parse_eligibility_batch.py [--source live|sample]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from planner.data_loader import load_courses  # noqa: E402
from planner.eligibility import CACHE_PATH, parse_notes  # noqa: E402
from planner.llm_client import get_client  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _load_existing(path: Path) -> dict:
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw.get("conditions", {}) if isinstance(raw, dict) else {}
    return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="階段 3：批次解讀限修條件")
    ap.add_argument("--source", choices=["live", "sample"], default=None,
                    help="資料來源（預設依 DATA_SOURCE，否則 sample）")
    ap.add_argument("--max-calls", type=int, default=300,
                    help="本次最多呼叫語言模型次數（預設 300）")
    ap.add_argument("--force", action="store_true", help="忽略既有快取，全部重做")
    args = ap.parse_args()

    client = get_client()
    courses, src = load_courses(args.source)
    print(f"資料來源：{src}；LLM_PROVIDER：{client.provider}")

    todo = [c for c in courses if c.notes and c.notes.strip()]
    existing = {} if args.force else _load_existing(CACHE_PATH)
    pending = [c for c in todo if c.id not in existing]

    print(f"備註非空課程：{len(todo)} 門；已快取：{len(existing)}；"
          f"待處理：{len(pending)} 門")
    print(f"預估本次呼叫次數：{min(len(pending), args.max_calls)}"
          f"（上限 --max-calls={args.max_calls}）")
    if client.provider == "mock":
        print("（mock 模式：不會真的呼叫 API，以關鍵字規則產生確定性結果）")

    conditions = dict(existing)
    done = 0
    for c in pending:
        if done >= args.max_calls:
            print(f"已達本次呼叫上限 {args.max_calls}，剩餘 {len(pending) - done} 門留待下次。")
            break
        cond = parse_notes(c.notes, client)
        conditions[c.id] = cond.to_dict()
        done += 1
        if done % 50 == 0:
            print(f"  進度 {done}/{min(len(pending), args.max_calls)} …")

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": src,
        "provider": client.provider,
        "count": len(conditions),
        "conditions": conditions,
    }
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # 簡單統計
    confs = {}
    for cond in conditions.values():
        confs[cond.get("confidence", "low")] = confs.get(cond.get("confidence", "low"), 0) + 1
    print(f"\n完成：本次新增 {done} 門；快取共 {len(conditions)} 門 "
          f"→ {CACHE_PATH.relative_to(PROJECT_ROOT)}")
    print(f"  confidence 分布：{confs}")


if __name__ == "__main__":
    main()
