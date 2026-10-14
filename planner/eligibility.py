# -*- coding: utf-8 -*-
"""限修條件解讀與比對（階段 3，AI + 確定性比對）。

流程：
1. 語言模型把「備註自由文字」解析成結構化條件（schema 見 EligibilityCondition）。
   - 失敗 / 無金鑰 → 退回 keyword_parse（非 AI 關鍵字規則），流程不中斷。
2. 用 match() 把條件與使用者資料（系所/年級/已修課程）比對，判為三狀態：
   符合 / 可能不符合 / 無法判斷，並附原因。

原則（CLAUDE.md）：
- 文字沒提到的條件不得自行推論；不確定歸「無法判斷」。
- 語言模型輸出一律經 schema 驗證，失敗退回非 AI 做法。
- 比對（年級/系所/先修）是確定性程式，不交給語言模型。
- 原始備註文字必須一併保留，供網頁顯示讓使用者自行確認。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .llm_client import LLMClient

# 三種判定狀態
MATCH = "符合"
MAYBE_NOT = "可能不符合"
UNKNOWN = "無法判斷"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = PROJECT_ROOT / "data" / "eligibility_cache.json"

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "大一": 1}


# ------------------------------------------------------------- 結構化條件
@dataclass
class EligibilityCondition:
    min_grade: int | None = None                       # 年級下限（1–7）
    allowed_depts: list[str] = field(default_factory=list)   # 限定系所
    prerequisites: list[str] = field(default_factory=list)   # 先修課程
    other_conditions: list[str] = field(default_factory=list)  # 其他條件（需人工確認）
    unparsed: str = ""                                  # 無法結構化的殘餘文字
    confidence: str = "low"                             # high | medium | low

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_empty(self) -> bool:
        return not (self.min_grade or self.allowed_depts or self.prerequisites
                    or self.other_conditions or self.unparsed.strip())


def _coerce_condition(raw: dict[str, Any]) -> EligibilityCondition | None:
    """驗證並轉換語言模型輸出為 EligibilityCondition；不合法回傳 None。"""
    if not isinstance(raw, dict):
        return None
    try:
        mg = raw.get("min_grade")
        mg = int(mg) if mg not in (None, "", "null") else None
        if mg is not None and not (1 <= mg <= 7):
            mg = None

        def as_str_list(v) -> list[str]:
            if v is None:
                return []
            if isinstance(v, str):
                v = [v]
            return [str(x).strip() for x in v if str(x).strip()]

        conf = str(raw.get("confidence", "low")).lower()
        if conf not in ("high", "medium", "low"):
            conf = "low"
        return EligibilityCondition(
            min_grade=mg,
            allowed_depts=as_str_list(raw.get("allowed_depts")),
            prerequisites=as_str_list(raw.get("prerequisites")),
            other_conditions=as_str_list(raw.get("other_conditions")),
            unparsed=str(raw.get("unparsed", "") or ""),
            confidence=conf,
        )
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------- 非 AI 關鍵字解析（fallback）
def keyword_parse(notes: str) -> EligibilityCondition:
    """用關鍵字規則把備註解析成條件（不呼叫 AI）。作為 fallback 與 mock 結果。"""
    cond = EligibilityCondition(confidence="low")
    if not notes or not notes.strip():
        return cond
    text = notes

    # 年級下限：如「限大三以上」「三年級(含)以上」「大二(含)以上」
    m = re.search(r"(?:限)?大?([一二三四五六七])年級(?:\(含\))?(?:以上)?", text)
    if not m:
        m = re.search(r"大([一二三四五六七])(?:\(含\))?以上", text)
    if m and m.group(1) in _CN_NUM:
        cond.min_grade = _CN_NUM[m.group(1)]

    # 限定系所：如「限資工系」「限通訊工程學系」「限本系」
    for dm in re.finditer(r"限([一-龥]{2,10}?(?:系|學系|系所|所))", text):
        name = dm.group(1)
        if name not in cond.allowed_depts:
            cond.allowed_depts.append(name)

    # 先修：如「須先修微積分」「先修資料結構」「須修畢XXX」
    _PRE_STOP = {"課程", "課", "科目", "者", "完畢", "畢業", "及格"}
    for pm in re.finditer(r"(?:須)?(?:先修|修畢)([一-龥\(\)A-Za-z0-9]{2,20})", text):
        name = pm.group(1).strip("。，、 ")
        if name and name not in _PRE_STOP and name not in cond.prerequisites:
            cond.prerequisites.append(name)

    # 其他條件（需人工確認）：方括號標記、教師同意、學制限制等
    for token in re.findall(r"\[([^\]]+)\]", text):
        cond.other_conditions.append(token.strip())
    for kw in ("經教師同意", "需教師同意", "教師同意", "限學士班", "限大學部",
               "限碩士班", "限研究生", "不開放初選", "限申請校學士", "碩博同修"):
        if kw in text and kw not in cond.other_conditions:
            cond.other_conditions.append(kw)

    if cond.min_grade or cond.allowed_depts or cond.prerequisites:
        cond.confidence = "medium"
    return cond


# ------------------------------------------------------------- AI 解析
_SYSTEM = (
    "你是協助解讀大學課程『限修條件』的助手。使用者會給你一段課程備註文字，"
    "請只萃取文字中明確提到的限修條件，輸出 JSON 物件，不要加任何說明或 Markdown。"
    "欄位：min_grade(整數年級下限或 null)、allowed_depts(限定系所字串陣列)、"
    "prerequisites(先修課程字串陣列)、other_conditions(其他條件字串陣列)、"
    "unparsed(無法歸類的原文片段字串)、confidence(high/medium/low)。"
    "嚴格要求：文字沒有明確提到的條件一律不要推論或填入；不確定就放進 other_conditions "
    "或 unparsed，並把 confidence 設為 low。"
)


def _build_messages(notes: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"課程備註：\n{notes}\n\n請輸出 JSON。"},
    ]


def parse_notes(notes: str, client: LLMClient) -> EligibilityCondition:
    """把單門課的備註解析成結構化條件。

    空備註 → 空條件。否則呼叫語言模型，輸出經 schema 驗證；
    失敗或 mock → 退回 keyword_parse。
    """
    if not notes or not notes.strip():
        return EligibilityCondition(confidence="high")  # 無備註 = 無限修條件
    fallback = keyword_parse(notes)
    raw = client.complete_json(_build_messages(notes), fallback=fallback.to_dict())
    cond = _coerce_condition(raw)
    return cond if cond is not None else fallback


# ------------------------------------------------------------- 使用者與比對
@dataclass
class UserProfile:
    dept: str | None = None                       # 系所（中文名）
    grade: int | None = None                      # 年級（1–7）
    completed_courses: list[str] = field(default_factory=list)  # 已修課名


@dataclass
class EligibilityResult:
    status: str                      # 符合 / 可能不符合 / 無法判斷
    reasons: list[str]
    condition: EligibilityCondition
    raw_notes: str                   # 原始備註，供網頁一併顯示

    def to_dict(self) -> dict[str, Any]:
        d = {
            "status": self.status,
            "reasons": self.reasons,
            "condition": self.condition.to_dict(),
            "raw_notes": self.raw_notes,
        }
        return d


def _dept_match(user_dept: str, allowed: str) -> bool:
    """系所模糊比對：允許互為子字串（如『通訊系』⊆『通訊工程學系』）。"""
    a = user_dept.replace("學系", "系").replace("系所", "系")
    b = allowed.replace("學系", "系").replace("系所", "系").replace("所", "")
    core = b.rstrip("系")
    return bool(core) and (core in a or a.rstrip("系") in b)


def match(condition: EligibilityCondition, user: UserProfile,
          raw_notes: str = "") -> EligibilityResult:
    """比對條件與使用者資料，判定三狀態並附原因。"""
    reasons: list[str] = []
    hard_fail = False      # 年級/系所明確不符
    soft_fail = False      # 先修可能缺
    has_unknown = False    # 有無法以現有資料確認的條件

    if condition.is_empty():
        return EligibilityResult(MATCH, ["此課程備註未載明特殊限修條件"],
                                 condition, raw_notes)

    # 年級
    if condition.min_grade is not None:
        if user.grade is None:
            has_unknown = True
            reasons.append(f"限{condition.min_grade}年級以上，但未提供你的年級")
        elif user.grade >= condition.min_grade:
            reasons.append(f"年級符合（需 ≥{condition.min_grade}，你是 {user.grade}）")
        else:
            hard_fail = True
            reasons.append(f"年級不足（需 ≥{condition.min_grade}，你是 {user.grade}）")

    # 系所
    if condition.allowed_depts:
        if not user.dept:
            has_unknown = True
            reasons.append(f"限定系所 {condition.allowed_depts}，但未提供你的系所")
        elif any(_dept_match(user.dept, d) for d in condition.allowed_depts):
            reasons.append(f"系所符合（限 {condition.allowed_depts}）")
        else:
            hard_fail = True
            reasons.append(f"系所可能不符（限 {condition.allowed_depts}，你是 {user.dept}）")

    # 先修課程
    for pre in condition.prerequisites:
        if any(_course_taken(pre, done) for done in user.completed_courses):
            reasons.append(f"已修先修課「{pre}」")
        else:
            soft_fail = True
            reasons.append(f"可能缺先修課「{pre}」（請自行確認是否已修或同等能力）")

    # 其他條件 / 殘餘文字 → 無法以系所/年級/已修課程確認
    for oc in condition.other_conditions:
        has_unknown = True
        reasons.append(f"需人工確認：{oc}")
    if condition.unparsed.strip():
        has_unknown = True
        reasons.append(f"備註尚有未解析內容：{condition.unparsed.strip()}")
    if condition.confidence == "low":
        has_unknown = True

    # 綜合判定：硬性不符 > 先修可能缺 > 有不確定 > 符合
    if hard_fail:
        status = MAYBE_NOT
    elif soft_fail:
        status = MAYBE_NOT
    elif has_unknown:
        status = UNKNOWN
    else:
        status = MATCH
    return EligibilityResult(status, reasons, condition, raw_notes)


def _course_taken(prereq: str, done: str) -> bool:
    """粗略判斷已修課是否涵蓋某先修課（去空白後互為子字串）。"""
    a = re.sub(r"\s", "", prereq)
    b = re.sub(r"\s", "", done)
    return bool(a) and bool(b) and (a in b or b in a)


# ------------------------------------------------------------- 快取 I/O
def load_cache(path: Path = CACHE_PATH) -> dict[str, EligibilityCondition]:
    """載入離線批次解析的結果（課號 → 條件）。檔案不存在回空 dict。"""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("conditions", raw) if isinstance(raw, dict) else {}
    out: dict[str, EligibilityCondition] = {}
    for cid, cond in items.items():
        c = _coerce_condition(cond)
        if c is not None:
            out[cid] = c
    return out


def evaluate(course_id: str, raw_notes: str, user: UserProfile,
             cache: dict[str, EligibilityCondition]) -> EligibilityResult:
    """網頁使用：用快取的條件（不即時呼叫 AI）比對使用者，回傳判定結果。"""
    cond = cache.get(course_id)
    if cond is None:
        cond = keyword_parse(raw_notes)  # 快取缺漏時的即時非 AI 退路
    return match(cond, user, raw_notes)
