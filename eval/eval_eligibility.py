# -*- coding: utf-8 -*-
"""限修條件解析評估（階段：評估）。

讀 eval/eligibility_labels.csv（由組員人工標註的 50 筆正確條件），對每筆的
原始備註呼叫 planner.eligibility.parse_notes，逐欄位比對正確率，列出錯誤案例，
結果寫入 outputs/eval/eligibility_report.{json,md}。

比對欄位：min_grade、allowed_depts、prerequisites、other_conditions。
- min_grade：整數相等（皆無亦算對）。
- 系所：正規化後（學系/系所/所 → 系）集合比對，忽略順序。
- 先修 / 其他條件：去空白後集合比對，忽略順序。

原則（CLAUDE.md）：
- README 數字須來自本腳本輸出，不得填寫示意數字。
- 尚未標註（label_* 全空）的列會跳過，並於報表標明已標註筆數。
- 預設依 LLM_PROVIDER（免金鑰用 mock=關鍵字規則）。

可獨立執行：
    python eval/make_eligibility_labels.py --source live   # 先產生空白檔給組員標
    LLM_PROVIDER=mock python eval/eval_eligibility.py       # 標註後計算正確率
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from planner.eligibility import parse_notes  # noqa: E402
from planner.llm_client import get_client  # noqa: E402


def _force_utf8_stdout() -> None:
    """命令列執行時把 stdout 轉為 UTF-8（import 時不執行，避免干擾 pytest 擷取）。"""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


LABELS_PATH = PROJECT_ROOT / "eval" / "eligibility_labels.csv"
OUT_DIR = PROJECT_ROOT / "outputs" / "eval"
LABEL_COLS = ["label_min_grade", "label_allowed_depts",
              "label_prerequisites", "label_other_conditions"]


def _norm_dept(s: str) -> str:
    return s.replace("學系", "系").replace("系所", "系").replace("所", "").strip()


def _split_list(cell: str) -> list[str]:
    return [x.strip() for x in (cell or "").split("|") if x.strip()]


def _row_is_labeled(row: dict) -> bool:
    return any((row.get(c) or "").strip() for c in LABEL_COLS)


def _min_grade_correct(label: str, cond) -> bool:
    label = (label or "").strip()
    expected = int(label) if label else None
    return expected == cond.min_grade


def _set_correct(labels: list[str], predicted: list[str], normalize=lambda x: x) -> bool:
    return {normalize(x) for x in labels} == {normalize(x) for x in predicted}


FIELDS = ["min_grade", "allowed_depts", "prerequisites", "other_conditions"]


def evaluate(rows: list[dict], client) -> dict:
    labeled = [r for r in rows if _row_is_labeled(r)]
    field_hit = {k: 0 for k in FIELDS}
    exact_rows = 0
    details = []

    for r in labeled:
        notes = r.get("raw_notes", "") or ""
        cond = parse_notes(notes, client)

        checks = {
            "min_grade": _min_grade_correct(r.get("label_min_grade", ""), cond),
            "allowed_depts": _set_correct(
                _split_list(r.get("label_allowed_depts", "")),
                cond.allowed_depts, _norm_dept),
            "prerequisites": _set_correct(
                _split_list(r.get("label_prerequisites", "")),
                cond.prerequisites, lambda x: x.replace(" ", "")),
            "other_conditions": _set_correct(
                _split_list(r.get("label_other_conditions", "")),
                cond.other_conditions),
        }
        wrong = [k for k, ok in checks.items() if not ok]
        for k, ok in checks.items():
            if ok:
                field_hit[k] += 1
        if not wrong:
            exact_rows += 1
        details.append({
            "course_id": r.get("course_id"),
            "name": r.get("name"),
            "raw_notes": notes,
            "predicted": cond.to_dict(),
            "label": {c: r.get(c, "") for c in LABEL_COLS},
            "wrong_fields": wrong,
        })

    n = len(labeled)
    field_acc = {k: (field_hit[k] / n if n else 0.0) for k in FIELDS}
    overall = sum(field_hit.values()) / (n * len(FIELDS)) if n else 0.0
    return {
        "provider": client.provider,
        "n_total_rows": len(rows),
        "n_labeled": n,
        "exact_match_rows": exact_rows,
        "exact_match_rate": (exact_rows / n if n else 0.0),
        "field_accuracy": field_acc,
        "overall_field_accuracy": overall,
        "details": details,
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_reports(result: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "eligibility_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 限修條件解析評估結果",
        "",
        f"- 產生日期：{date.today().isoformat()}",
        f"- LLM_PROVIDER：`{result['provider']}`",
        f"- 已標註 / 總列數：{result['n_labeled']} / {result['n_total_rows']}",
    ]
    if result["n_labeled"] == 0:
        lines += ["",
                  "> ⚠ 目前尚無已標註的資料列。請組員先填寫 "
                  "`eval/eligibility_labels.csv` 的 `label_*` 欄位後再執行本腳本。"]
    else:
        lines += [
            f"- 全部欄位皆正確之列：**{_pct(result['exact_match_rate'])}**"
            f"（{result['exact_match_rows']}/{result['n_labeled']}）",
            f"- 平均欄位正確率：**{_pct(result['overall_field_accuracy'])}**",
            "",
            "## 各欄位正確率",
            "",
            "| 欄位 | 正確率 |",
            "|---|---|",
        ]
        for field, acc in result["field_accuracy"].items():
            lines.append(f"| `{field}` | {_pct(acc)} |")

        errors = [d for d in result["details"] if d["wrong_fields"]]
        lines += ["", f"## 錯誤案例（{len(errors)} 筆）", ""]
        if not errors:
            lines.append("（無錯誤案例）")
        for d in errors:
            lines.append(f"- **{d['course_id']} {d['name']}**：{d['raw_notes']}")
            lines.append(f"  - 錯誤欄位：{', '.join(d['wrong_fields'])}")
            lines.append(f"  - 標註：{d['label']}")
            lines.append(f"  - 解析：min_grade={d['predicted'].get('min_grade')}, "
                         f"allowed_depts={d['predicted'].get('allowed_depts')}, "
                         f"prerequisites={d['predicted'].get('prerequisites')}, "
                         f"other_conditions={d['predicted'].get('other_conditions')}")
    lines.append("")
    (OUT_DIR / "eligibility_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="評估：限修條件解析正確率")
    ap.add_argument("--provider", default=None, help="覆寫 LLM_PROVIDER（如 mock）")
    ap.add_argument("--labels", type=Path, default=LABELS_PATH, help="標註 CSV 路徑")
    args = ap.parse_args()
    _force_utf8_stdout()

    if not args.labels.exists():
        print(f"找不到標註檔 {args.labels.relative_to(PROJECT_ROOT)}。"
              f"請先執行 eval/make_eligibility_labels.py 產生空白檔並完成標註。")
        return

    with args.labels.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    client = get_client(args.provider)
    print(f"標註檔列數：{len(rows)}；LLM_PROVIDER：{client.provider}")

    result = evaluate(rows, client)
    write_reports(result)

    print(f"已標註：{result['n_labeled']} / {result['n_total_rows']} 列")
    if result["n_labeled"] == 0:
        print("尚無標註，無法計算正確率。請先填寫 label_* 欄位。")
    else:
        print(f"全欄位皆正確率：{_pct(result['exact_match_rate'])}"
              f"（{result['exact_match_rows']}/{result['n_labeled']}）")
        print(f"平均欄位正確率：{_pct(result['overall_field_accuracy'])}")
        for field, acc in result["field_accuracy"].items():
            print(f"  {field:18s} {_pct(acc)}")
    print(f"→ 報表已寫入 {OUT_DIR.relative_to(PROJECT_ROOT)}/eligibility_report.(json|md)")


if __name__ == "__main__":
    main()
