# -*- coding: utf-8 -*-
"""評估模組測試：比對函式與整體計算（不呼叫 API，用 mock）。"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "eval"


def _load(name: str):
    """由檔案路徑載入 eval/ 下的腳本（該目錄不是套件）。"""
    spec = importlib.util.spec_from_file_location(name, EVAL_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ep = _load("eval_preferences")
ee = _load("eval_eligibility")

from planner.llm_client import get_client  # noqa: E402

MOCK = get_client("mock")


# ----------------------------------------------------------- 偏好比對
def test_field_correct_ignores_order():
    assert ep.field_correct([[1, "1"], [2, "1"]], [[2, "1"], [1, "1"]], "pairs")
    assert ep.field_correct([5, 2], [2, 5], "ints")
    assert ep.field_correct(["日語", "英語"], ["英語", "日語"], "strs")


def test_field_correct_scalar_and_mismatch():
    assert ep.field_correct(None, None, "scalar")
    assert ep.field_correct(16, 16.0, "scalar")
    assert not ep.field_correct(16, 18, "scalar")
    assert not ep.field_correct([5], [5, 6], "ints")


def test_preferences_evaluate_counts():
    cases = [
        {"id": "a", "text": "不要早八", "expected": {
            "avoid_periods": [[1, "1"], [2, "1"], [3, "1"], [4, "1"], [5, "1"]],
            "free_days": [], "min_credits": None, "max_credits": None,
            "topics": [], "ge_domains": [], "languages": []}},
        {"id": "b", "text": "想上日文課", "expected": {
            "avoid_periods": [], "free_days": [], "min_credits": None,
            "max_credits": None, "topics": [], "ge_domains": [],
            "languages": ["日語"]}},
    ]
    res = ep.evaluate(cases, MOCK)
    assert res["n_cases"] == 2
    # 這兩句關鍵字規則都能完全解析 → 全對
    assert res["exact_match_rows"] == 2
    assert res["field_accuracy"]["languages"] == 1.0


# ----------------------------------------------------------- 限修比對
def test_norm_dept_and_split():
    assert ee._norm_dept("通訊工程學系") == "通訊工程系"
    assert ee._split_list("微積分|線性代數") == ["微積分", "線性代數"]
    assert ee._split_list("") == []


def test_min_grade_and_set_correct():
    class C:
        min_grade = 3
    assert ee._min_grade_correct("3", C())
    assert not ee._min_grade_correct("", C())
    assert ee._set_correct(["通訊工程系"], ["通訊工程學系"], ee._norm_dept)
    assert ee._set_correct(["微 積分"], ["微積分"], lambda x: x.replace(" ", ""))


def test_eligibility_evaluate_skips_unlabeled():
    rows = [
        {"course_id": "1", "name": "甲", "raw_notes": "限大三以上",
         "label_min_grade": "3", "label_allowed_depts": "",
         "label_prerequisites": "", "label_other_conditions": ""},
        {"course_id": "2", "name": "乙", "raw_notes": "限資工系",
         "label_min_grade": "", "label_allowed_depts": "",
         "label_prerequisites": "", "label_other_conditions": ""},  # 全空 → 跳過
    ]
    res = ee.evaluate(rows, MOCK)
    assert res["n_total_rows"] == 2
    assert res["n_labeled"] == 1
    # 「限大三以上」關鍵字規則可解析出 min_grade=3 → 該欄正確
    assert res["field_accuracy"]["min_grade"] == 1.0
