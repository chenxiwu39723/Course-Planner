# -*- coding: utf-8 -*-
"""產生限修條件的「待標註空白檔」eval/eligibility_labels.csv（階段：評估）。

從資料中挑出備註非空的課程，隨機抽 50 筆（random_state=42，可重現），
輸出一份 CSV：前段為課程資訊與原始備註，後段為留白的標註欄位，
交由組員人工填入『正確』的限修條件。之後 eval_eligibility.py 會用這份
標註檔計算語言模型解析的正確率（CLAUDE.md：README 數字須來自實際評估）。

標註欄位（列表欄位請以半形「|」分隔，沒有就留空）：
    label_min_grade         年級下限整數（1–7），無則留空
    label_allowed_depts     限定系所，如「通訊系|電機系」
    label_prerequisites     先修課程，如「微積分|線性代數」
    label_other_conditions  其他須人工確認的條件，如「需教師同意」

為保護已標註內容，檔案已存在時不覆寫（除非加 --force）。CSV 以 utf-8-sig
輸出，方便以 Excel 直接開啟顯示中文。

可獨立執行：
    python eval/make_eligibility_labels.py --source live
    python eval/make_eligibility_labels.py --source sample -n 50
"""
from __future__ import annotations

import argparse
import csv
import io
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from planner.data_loader import load_courses  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

OUT_PATH = PROJECT_ROOT / "eval" / "eligibility_labels.csv"
RANDOM_STATE = 42

INFO_COLS = ["course_id", "code", "name", "dept", "raw_notes"]
LABEL_COLS = ["label_min_grade", "label_allowed_depts",
              "label_prerequisites", "label_other_conditions"]


def main() -> None:
    ap = argparse.ArgumentParser(description="產生限修條件待標註空白檔")
    ap.add_argument("--source", choices=["live", "sample"], default=None,
                    help="資料來源（預設依 DATA_SOURCE，否則 sample）")
    ap.add_argument("-n", "--num", type=int, default=50, help="抽樣筆數（預設 50）")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="輸出 CSV 路徑")
    ap.add_argument("--force", action="store_true", help="覆寫既有檔案（會蓋掉已標註內容）")
    args = ap.parse_args()

    if args.out.exists() and not args.force:
        print(f"[略過] {args.out.relative_to(PROJECT_ROOT)} 已存在；"
              f"為避免蓋掉已標註內容不覆寫。要重產請加 --force。")
        return

    courses, src = load_courses(args.source)
    with_notes = [c for c in courses if c.notes and c.notes.strip()]
    print(f"資料來源：{src}；備註非空課程：{len(with_notes)} 門")
    if not with_notes:
        print("沒有備註非空的課程，無法產生標註檔。")
        return

    rng = random.Random(RANDOM_STATE)
    k = min(args.num, len(with_notes))
    # 先依流水號排序再抽樣，確保同一份資料每次抽到相同的 50 筆
    pool = sorted(with_notes, key=lambda c: c.id)
    picked = rng.sample(pool, k)
    picked.sort(key=lambda c: c.id)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(INFO_COLS + LABEL_COLS)
        for c in picked:
            notes = " ".join(c.notes.split())  # 壓成單行，避免換行破壞 CSV
            w.writerow([c.id, c.code, c.name, c.dept, notes] + ["" for _ in LABEL_COLS])

    print(f"已寫入 {k} 筆待標註資料 → {args.out.relative_to(PROJECT_ROOT)}")
    print("請組員填寫 label_* 欄位（列表欄位以半形「|」分隔），"
          "再執行 eval/eval_eligibility.py 計算正確率。")


if __name__ == "__main__":
    main()
