# -*- coding: utf-8 -*-
"""白話偏好解析評估（階段 4）。

讀 eval/preference_cases.json 的 20 句白話偏好與預期條件，對每句呼叫
planner.preferences.parse_preferences，逐欄位比對正確率，列出錯誤案例，
結果寫入 outputs/eval/preferences_report.{json,md}。

原則（CLAUDE.md）：
- README 的評估數字必須來自本腳本的輸出，不得填寫示意數字。
- 預設依環境變數 LLM_PROVIDER；免金鑰可用 LLM_PROVIDER=mock（關鍵字規則）。
- 隨機性固定，無隨機成分（確定性）。

可獨立執行：
    python eval/eval_preferences.py              # 依 LLM_PROVIDER（預設 azure_openai）
    LLM_PROVIDER=mock python eval/eval_preferences.py
    python eval/eval_preferences.py --provider mock
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from planner.llm_client import get_client  # noqa: E402
from planner.preferences import parse_preferences  # noqa: E402


def _force_utf8_stdout() -> None:
    """命令列執行時把 stdout 轉為 UTF-8（import 時不執行，避免干擾 pytest 擷取）。"""
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


CASES_PATH = PROJECT_ROOT / "eval" / "preference_cases.json"
OUT_DIR = PROJECT_ROOT / "outputs" / "eval"

# 比對的欄位與型別。pairs=無序的 [day, period] 配對；ints/strs=無序集合；scalar=純量
FIELD_KINDS = {
    "avoid_periods": "pairs",
    "free_days": "ints",
    "min_credits": "scalar",
    "max_credits": "scalar",
    "topics": "strs",
    "ge_domains": "strs",
    "languages": "strs",
}


def _norm(value, kind):
    """把欄位值正規化成可直接比較的形式（集合比對忽略順序）。"""
    if kind == "scalar":
        return None if value in (None, "", "null") else float(value)
    if kind == "pairs":
        return frozenset((int(d), str(p)) for d, p in (value or []))
    if kind == "ints":
        return frozenset(int(x) for x in (value or []))
    if kind == "strs":
        return frozenset(str(x).strip() for x in (value or []) if str(x).strip())
    raise ValueError(kind)


def field_correct(expected, predicted, kind) -> bool:
    """單一欄位是否正確（集合/純量比對）。"""
    return _norm(expected, kind) == _norm(predicted, kind)


def evaluate(cases: list[dict], client) -> dict:
    """對每筆測資解析並比對，回傳統計結果。"""
    field_hit = {k: 0 for k in FIELD_KINDS}
    exact_rows = 0
    details = []
    for case in cases:
        text = case["text"]
        expected = case["expected"]
        spec = parse_preferences(text, client).to_dict()

        wrong_fields = []
        for field, kind in FIELD_KINDS.items():
            ok = field_correct(expected.get(field), spec.get(field), kind)
            if ok:
                field_hit[field] += 1
            else:
                wrong_fields.append(field)
        if not wrong_fields:
            exact_rows += 1
        details.append({
            "id": case.get("id"),
            "text": text,
            "expected": expected,
            "predicted": spec,
            "wrong_fields": wrong_fields,
        })

    n = len(cases)
    field_acc = {k: (field_hit[k] / n if n else 0.0) for k in FIELD_KINDS}
    overall_field_acc = sum(field_hit.values()) / (n * len(FIELD_KINDS)) if n else 0.0
    return {
        "n_cases": n,
        "provider": client.provider,
        "exact_match_rows": exact_rows,
        "exact_match_rate": (exact_rows / n if n else 0.0),
        "field_accuracy": field_acc,
        "overall_field_accuracy": overall_field_acc,
        "details": details,
    }


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def write_reports(result: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "preferences_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 白話偏好解析評估結果",
        "",
        f"- 產生日期：{date.today().isoformat()}",
        f"- LLM_PROVIDER：`{result['provider']}`",
        f"- 測資筆數：{result['n_cases']}",
        f"- 整句全對率（所有欄位皆正確）：**{_pct(result['exact_match_rate'])}**"
        f"（{result['exact_match_rows']}/{result['n_cases']}）",
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
        lines.append(f"- **{d['id']}**「{d['text']}」錯誤欄位：{', '.join(d['wrong_fields'])}")
        for f in d["wrong_fields"]:
            lines.append(f"  - `{f}`：預期 `{d['expected'].get(f)}` → 實得 `{d['predicted'].get(f)}`")
    lines.append("")
    (OUT_DIR / "preferences_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="階段 4：白話偏好解析評估")
    ap.add_argument("--provider", default=None,
                    help="覆寫 LLM_PROVIDER（如 mock）；預設依環境變數")
    ap.add_argument("--cases", type=Path, default=CASES_PATH, help="測資 JSON 路徑")
    args = ap.parse_args()
    _force_utf8_stdout()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    client = get_client(args.provider)
    print(f"測資：{len(cases)} 筆；LLM_PROVIDER：{client.provider}")

    result = evaluate(cases, client)
    write_reports(result)

    print(f"整句全對率：{_pct(result['exact_match_rate'])}"
          f"（{result['exact_match_rows']}/{result['n_cases']}）")
    print(f"平均欄位正確率：{_pct(result['overall_field_accuracy'])}")
    print("各欄位正確率：")
    for field, acc in result["field_accuracy"].items():
        print(f"  {field:16s} {_pct(acc)}")
    print(f"→ 報表已寫入 {OUT_DIR.relative_to(PROJECT_ROOT)}/preferences_report.(json|md)")


if __name__ == "__main__":
    main()
