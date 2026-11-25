# -*- coding: utf-8 -*-
"""白話偏好解析（階段 4，AI + 確定性 fallback）。

把使用者的白話需求（例如「不要早八、週五想空著、想學資料分析相關、通識 4 學分」）
解析成條件 JSON：
    {avoid_periods, free_days, min_credits, max_credits, topics, ge_domains, languages}

原則（CLAUDE.md）：
- 解析後的條件要顯示在網頁上讓使用者確認/修改，再送進 solver。
- LLM 輸出經 schema 驗證；失敗或 mock → 退回關鍵字規則（非 AI），不中斷。
- topics 交給語意搜尋；其餘轉成 solver 的硬性/篩選條件。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .llm_client import LLMClient
from . import timeslots as ts
from .solver import Preferences

# 中文星期 → 星期碼
_WEEKDAY = {"日": 0, "天": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
# 常見語言講法 → 標準語言名（與課程查詢系統一致）
_LANG_ALIAS = {
    "日文": "日語", "日語": "日語", "英文": "英語", "英語": "英語",
    "法文": "法語", "法語": "法語", "德文": "德語", "德語": "德語",
    "西班牙文": "西班牙語", "西班牙語": "西班牙語", "韓文": "韓語", "韓語": "韓語",
    "台語": "台語", "客語": "客語",
}


@dataclass
class PreferenceSpec:
    """解析後的偏好條件（可 JSON 序列化，供網頁顯示/修改）。"""

    avoid_periods: list[list] = field(default_factory=list)   # [[day, period], ...]
    free_days: list[int] = field(default_factory=list)
    min_credits: float | None = None
    max_credits: float | None = None
    topics: list[str] = field(default_factory=list)
    ge_domains: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_solver_prefs(self, **overrides) -> Preferences:
        """轉成 solver.Preferences（topics 不進 solver，留給語意搜尋）。"""
        p = Preferences(
            min_credits=self.min_credits if self.min_credits is not None else 0.0,
            max_credits=self.max_credits if self.max_credits is not None else 999.0,
            avoid_periods=[(int(d), str(pp)) for d, pp in self.avoid_periods],
            free_days=list(self.free_days),
            ge_domains=self.ge_domains or None,
            languages=self.languages or None,
            # 真實資料含週末開課（週六/日），排課範圍納入全週；
            # 不想上週末者可用 free_days（如「週六想空著」）排除。
            days=[1, 2, 3, 4, 5, 6, 0],
        )
        for k, v in overrides.items():
            setattr(p, k, v)
        return p


# ------------------------------------------------------------- schema 驗證
MAX_CREDITS_LIMIT = 60.0    # 學分合理上限（擋 NaN、inf、負數與誇張值）


def _coerce_spec(raw: dict[str, Any], strict: bool = False) -> PreferenceSpec | None:
    """把 dict 轉成 PreferenceSpec。

    - 預設（LLM 輸出）：逐欄位容錯，只捨棄不合法的欄位或項目，不會整份丟掉。
    - strict=True（使用者送來的條件）：任何不合法處都 raise ValueError，
      讓 API 回 400 告知使用者，而不是默默忽略。
    """
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("preferences 必須是物件")
        return None
    errors: list[str] = []

    def num(key):
        v = raw.get(key)
        if v in (None, "", "null"):
            return None
        try:
            x = float(v)
        except (ValueError, TypeError):
            x = None
        if isinstance(v, bool) or x is None or not math.isfinite(x) or not 0 <= x <= MAX_CREDITS_LIMIT:
            errors.append(f"{key} 必須是 0～{MAX_CREDITS_LIMIT:g} 的數字")
            return None
        return x

    def as_list(key):
        v = raw.get(key)
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, (list, tuple)):
            return list(v)
        errors.append(f"{key} 必須是陣列")
        return []

    def str_list(key):
        return [str(x).strip() for x in as_list(key)
                if isinstance(x, (str, int, float)) and str(x).strip()]

    def day(x):
        if isinstance(x, bool):
            return None
        try:
            d = int(x)
        except (ValueError, TypeError):
            return None
        return d if 0 <= d <= 6 and str(x).strip() == str(d) else None

    avoid = []
    for item in as_list("avoid_periods"):
        if isinstance(item, (list, tuple)) and len(item) == 2:
            d, p = day(item[0]), str(item[1])
            if d is not None and p in ts.PERIOD_TIMES:
                if [d, p] not in avoid:
                    avoid.append([d, p])
                continue
        errors.append(f"avoid_periods 含無效時段：{item!r}")

    free_days = []
    for x in as_list("free_days"):
        d = day(x)
        if d is None:
            errors.append(f"free_days 含無效星期：{x!r}")
        elif d not in free_days:
            free_days.append(d)

    min_c, max_c = num("min_credits"), num("max_credits")
    if min_c is not None and max_c is not None and min_c > max_c:
        errors.append("min_credits 不可大於 max_credits")
        min_c = max_c = None
    topics, ge_domains, languages = str_list("topics"), str_list("ge_domains"), str_list("languages")

    if strict and errors:
        raise ValueError("偏好條件有誤：" + "；".join(errors))
    return PreferenceSpec(
        avoid_periods=avoid,
        free_days=free_days,
        min_credits=min_c,
        max_credits=max_c,
        topics=topics,
        ge_domains=ge_domains,
        languages=[_LANG_ALIAS.get(x, x) for x in languages],
    )


# ------------------------------------------------------------- 非 AI 關鍵字解析
def keyword_parse(text: str) -> PreferenceSpec:
    """用關鍵字規則解析白話偏好（不呼叫 AI）。作為 fallback 與 mock 結果。"""
    spec = PreferenceSpec()
    if not text or not text.strip():
        return spec
    days = ts.DEFAULT_DAYS

    # 早八 / 第一節
    if re.search(r"早八|第一節|8點|８點|八點", text):
        spec.avoid_periods += [[d, "1"] for d in days]
    # 中午 / 午休
    if re.search(r"午休|中午", text) and re.search(r"不|避|空", text):
        spec.avoid_periods += [[d, "Z"] for d in days]
    # 晚上 / 夜間
    if re.search(r"晚上|夜間|夜晚", text) and re.search(r"不|避|沒", text):
        spec.avoid_periods += [[d, p] for d in days for p in ("A", "B", "C", "D")]

    # 想空的星期：如「週五想空著」「星期二不要排課」「禮拜四空堂」
    for m in re.finditer(r"(?:週|周|星期|禮拜)([日天一二三四五六])", text):
        wd = _WEEKDAY.get(m.group(1))
        if wd is None:
            continue
        # 該星期附近有「空/不排/不要」才視為想空的日子
        span = text[max(0, m.start() - 2): m.end() + 6]
        if re.search(r"空|不排|不要|沒課|放假", span) and wd not in spec.free_days:
            spec.free_days.append(wd)

    # 學分範圍
    rng = re.search(r"(\d+)\s*[到~\-至]\s*(\d+)\s*學分", text)
    if rng:
        spec.min_credits, spec.max_credits = float(rng.group(1)), float(rng.group(2))
    else:
        m = re.search(r"(?:至少|最少|不低於)\s*(\d+)\s*學分", text)
        if m:
            spec.min_credits = float(m.group(1))
        m = re.search(r"(?:最多|至多|不超過|以下|不要超過)\s*(\d+)\s*學分", text)
        if m:
            spec.max_credits = float(m.group(1))
        m = re.search(r"(?:總共|共|想修|修|要)\s*(\d+)\s*學分", text)
        if m and spec.min_credits is None and spec.max_credits is None:
            spec.max_credits = float(m.group(1))
        # 最後退路：出現「N 學分」但前面沒有範圍/動詞者，視為學分上限，
        # 以免完全沒有上限時 solver 把整週塞滿（如「通識 4 學分」）。
        if spec.min_credits is None and spec.max_credits is None:
            m = re.search(r"(\d+)\s*學分", text)
            if m:
                spec.max_credits = float(m.group(1))

    # 語言
    for alias, std in _LANG_ALIAS.items():
        if alias in text and std not in spec.languages:
            spec.languages.append(std)

    # 主題（想學 X / 對 X 有興趣 / X 相關）
    raw_topics: list[str] = []
    for m in re.finditer(r"(?:想學|想修|學習|對)\s*([一-龥A-Za-z0-9]{2,12})", text):
        raw_topics.append(m.group(1))
    for m in re.finditer(r"([一-龥A-Za-z0-9]{2,12})(?:相關|方面|領域)", text):
        raw_topics.append(m.group(1))
    spec.topics = _clean_topics(raw_topics)

    return spec


def _clean_topics(raw: list[str]) -> list[str]:
    """清理主題：去掉開頭動詞與結尾修飾、去重、移除彼此的子字串。"""
    cleaned = []
    for t in raw:
        t = re.sub(r"^(?:想學|想修|學習|對)", "", t).strip()
        t = re.sub(r"(?:相關|方面|領域)$", "", t).strip()
        if len(t) >= 2 and t not in ("學分",):
            cleaned.append(t)
    # 移除是其他主題子字串者（保留較長、較具體的）
    result: list[str] = []
    for t in sorted(set(cleaned), key=len, reverse=True):
        if not any(t != o and t in o for o in result):
            result.append(t)
    return result


# ------------------------------------------------------------- AI 解析
_SYSTEM = (
    "你是協助把學生的白話選課需求轉成結構化條件的助手。請只根據文字明確表達的需求，"
    "輸出 JSON 物件，不要加任何說明或 Markdown。欄位："
    "avoid_periods（要避開的時段，陣列，每項為 [星期碼, 節次碼]；星期碼 0=日..6=六；"
    "節次碼之一：1 2 3 4 Z 5 6 7 8 9 A B C D E F；『早八』=第1節，整週則列出週一到週五）、"
    "free_days（想整天空著的星期碼陣列）、min_credits、max_credits（數字或 null）、"
    "topics（想學的主題關鍵詞陣列）、ge_domains（想要的通識領域陣列）、"
    "languages（想要的授課語言陣列，如『日語』）。"
    "文字沒提到的欄位給空陣列或 null，不要自行臆測。"
)


def _build_messages(text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"學生需求：\n{text}\n\n請輸出 JSON。"},
    ]


def parse_preferences(text: str, client: LLMClient) -> PreferenceSpec:
    """把白話偏好解析成 PreferenceSpec。失敗/mock → 退回 keyword_parse。"""
    if not text or not text.strip():
        return PreferenceSpec()
    fallback = keyword_parse(text)
    raw = client.complete_json(_build_messages(text), fallback=fallback.to_dict())
    spec = _coerce_spec(raw)
    return spec if spec is not None else fallback
