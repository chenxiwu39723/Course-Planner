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
DISTRIBUTION_CACHE_PATH = PROJECT_ROOT / "data" / "distribution_cache.json"

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}

# 常見系所簡稱 → 正式名稱核心（去掉「學系／系」）；備註常寫「限資工系」
_DEPT_ALIASES = {
    "資工": "資訊工程", "資管": "資訊管理", "電機": "電機工程", "通訊": "通訊工程",
    "化材": "化學工程與材料工程", "機械": "機械工程", "土木": "土木工程",
    "企管": "企業管理", "財金": "財務金融", "光電": "光電科學與工程",
    "生醫": "生醫科學與工程", "太空": "太空科學與工程", "大氣": "大氣科學",
    "地科": "地球科學", "英文": "英美語文", "法文": "法國語文", "中文": "中國文學",
    "客家": "客家語文暨社會科學", "法政": "法律與政府", "生科": "生命科學",
}


# ------------------------------------------------------------- 結構化條件
@dataclass
class EligibilityCondition:
    min_grade: int | None = None                       # 年級下限（1–7）
    max_grade: int | None = None                       # 年級上限（如「限二年級」= 2～2）
    allowed_depts: list[str] = field(default_factory=list)   # 限定系所
    prerequisites: list[str] = field(default_factory=list)   # 先修課程
    other_conditions: list[str] = field(default_factory=list)  # 其他條件（需人工確認）
    unparsed: str = ""                                  # 無法結構化的殘餘文字
    confidence: str = "low"                             # high | medium | low

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def is_empty(self) -> bool:
        return not (self.min_grade or self.max_grade or self.allowed_depts
                    or self.prerequisites or self.other_conditions or self.unparsed.strip())


def _coerce_condition(raw: dict[str, Any]) -> EligibilityCondition | None:
    """驗證並轉換語言模型輸出為 EligibilityCondition；不合法回傳 None。"""
    if not isinstance(raw, dict):
        return None
    try:
        def grade(key):
            g = raw.get(key)
            g = int(g) if g not in (None, "", "null") else None
            return g if g is not None and 1 <= g <= 7 else None

        mg, xg = grade("min_grade"), grade("max_grade")
        if mg is not None and xg is not None and mg > xg:
            mg = xg = None

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
            max_grade=xg,
            allowed_depts=as_str_list(raw.get("allowed_depts")),
            prerequisites=as_str_list(raw.get("prerequisites")),
            other_conditions=as_str_list(raw.get("other_conditions")),
            unparsed=str(raw.get("unparsed", "") or ""),
            confidence=conf,
        )
    except (ValueError, TypeError):
        return None


# ------------------------------------------------------------- 非 AI 關鍵字解析（fallback）
_SEP = r"\s*[、,，及和與]?\s*"
# 「限三、四年級」「二三四年級」「二年級、三年級」
_GRADE_NJ = rf"(?:[一二三四五六七](?:年級)?{_SEP})*[一二三四五六七]年級"
# 「大二、大三、大四」「大三大四」
_GRADE_DA = rf"大[一二三四](?:{_SEP}大[一二三四])*"
_GRADE_RE = re.compile(
    rf"(?P<pre>限|僅供|僅限)?(?P<seq>{_GRADE_DA}|{_GRADE_NJ})"
    r"(?P<incl>\s*[\(（]含[\)）])?(?P<up>\s*以上)?"
)
_GRADE_CONTEXT_BEFORE = re.compile(r"(?:系|部|院|班)$")
_GRADE_CONTEXT_AFTER = re.compile(r"^(?:可修|可選修|且|之學生|學生|同學|選修|，|,|。|$)")


def _parse_grades(text: str) -> tuple[int | None, int | None]:
    """取第一個「明確是修課資格」的年級描述，回傳 (下限, 上限)。

    - 「限二年級」→ (2, 2)；「限三、四年級」→ (3, 4)；「大二(含)以上」→ (2, None)
    - 後面接碩博／研究生（如「限四年級和碩博」）→ 不設上限
    - 研究生年級（「博士生一年級」）、「準大一」、「大一體育」等描述性文字略過
    """
    for m in _GRADE_RE.finditer(text):
        before, after = text[max(0, m.start() - 3):m.start()], text[m.end():m.end() + 12]
        if re.search(r"(?:士生|研究生|碩|博|準)$", before):
            continue
        explicit = m.group("pre") or m.group("up")
        if m.group("seq").startswith("大"):
            if not (explicit or re.match(r"^(?:可修|可選修)", after)
                    or (_GRADE_CONTEXT_BEFORE.search(before) and re.match(r"^(?:學生|生|同學)", after))):
                continue
        elif not (explicit or _GRADE_CONTEXT_BEFORE.search(before)
                  or _GRADE_CONTEXT_AFTER.match(after)):
            continue
        grades = [_CN_NUM[ch] for ch in re.sub(r"年級|大", "", m.group("seq")) if ch in _CN_NUM]
        if not grades:
            continue
        lo, hi = min(grades), max(grades)
        if m.group("up") or re.search(r"碩|博|研究生", after):
            hi = None
        elif len(grades) == 1 and not m.group("pre") and re.match(r"^(?:可修|可選修)", after):
            hi = None                     # 「大三可選修」：未寫「限」，視為下限
        elif hi == 4 and re.search(r"碩博同修|研究生", text):
            hi = None                     # 大學部年級限制不排除同修的研究生
        return lo, hi
    return None, None


_PRE_RE = re.compile(r"(建議)?(?:須|需|請|限)?(?:先修習?|修畢)(?:課程)?[:：]?\s*([^，。,；;\s\[\]]{1,40})")
_PRE_CUT = re.compile(r"(?:之|的同學|的學生|同學|學生|者|且|選修|[一二三四]年級)")
_PRE_STOP = {"課程", "課", "科目", "者", "完畢", "畢業", "及格"}
_COURSE_CODE = re.compile(r"[\(（]?[A-Z]{2}\d{4}[\)）]?")


def _parse_prerequisites(text: str, cond: EligibilityCondition) -> None:
    """先修：如「須先修普化」「先修EG1001工程程式設計」「限修畢A或B」。

    - 「建議先修」不是硬性條件 → other_conditions
    - 計數或擇一類（「兩門…」「二擇一」）無法用已修課名比對 → other_conditions
    - 課號只在沒有課名時保留；「、」分隔視為多門都要修
    """
    def trim(s: str) -> str:
        """去掉前後標點與「不成對」的括號（保留「微積分(一)」這種成對括號）。"""
        s = s.strip("。，、 ").lstrip(")）")
        opens, closes = lambda x: x.count("(") + x.count("（"), lambda x: x.count(")") + x.count("）")
        while s and s[0] in "(（" and opens(s) > closes(s):
            s = s[1:]
        while s and s[-1] in ")）" and closes(s) > opens(s):
            s = s[:-1]
        return s.strip("。，、 ")

    for pm in _PRE_RE.finditer(text):
        if text[max(0, pm.start() - 4):pm.start()].endswith(("同修(含", "同修（含")):
            note = f"同修或先修：{trim(_COURSE_CODE.sub('', pm.group(2)))}"
            if note not in cond.other_conditions:
                cond.other_conditions.append(note)
            continue
        recommend = bool(pm.group(1))
        body = _PRE_CUT.split(pm.group(2), 1)[0]
        for part in body.split("、"):
            name = trim(_COURSE_CODE.sub("", part))
            if len(re.sub(r"[^一-龥]", "", name)) < 2:
                name = trim(part)                        # 只有課號（如 CM1009）
            if len(name) < 2 or name in _PRE_STOP:
                continue
            if (re.search(r"[兩二三四五六七八九十\d]門|擇一", name)
                    or name.count("(") != name.count(")")):
                note = f"先修：{body}"
            elif recommend:
                note = f"建議先修：{name}"
            else:
                if name not in cond.prerequisites:
                    cond.prerequisites.append(name)
                continue
            if note not in cond.other_conditions:
                cond.other_conditions.append(note)


def keyword_parse(notes: str) -> EligibilityCondition:
    """用關鍵字規則把備註解析成條件（不呼叫 AI）。作為 fallback 與 mock 結果。"""
    cond = EligibilityCondition(confidence="low")
    if not notes or not notes.strip():
        return cond
    text = notes

    # 年級：如「限大三以上」「三年級(含)以上」「限二年級」「限三、四年級」
    cond.min_grade, cond.max_grade = _parse_grades(text)

    # 限定系所：如「限資工系」「限通訊工程學系」；並處理「、」並列多系所
    _DEPT = r"[一-龥]{2,12}?(?:系|學系|系所|所)"
    for dm in re.finditer(rf"限({_DEPT}(?:、{_DEPT})*)", text):
        for name in dm.group(1).split("、"):
            name = name.strip()
            if not name or name in cond.allowed_depts:
                continue
            if name.startswith(("本", "該")):      # 「限該授課教授所」：無法得知是哪個系所
                if f"限{name}" not in cond.other_conditions:
                    cond.other_conditions.append(f"限{name}")
                continue
            cond.allowed_depts.append(name)
    for um in re.finditer(r"限(本|該)(系|所)", text):  # 「限本所博士生」
        if um.group(0) not in cond.other_conditions:
            cond.other_conditions.append(um.group(0))

    _parse_prerequisites(text, cond)

    # 其他條件（需人工確認）：方括號標記、教師同意、學制限制等
    for token in re.findall(r"\[([^\]]+)\]", text):
        cond.other_conditions.append(token.strip())
    for kw in ("經教師同意", "需教師同意", "教師同意", "限學士班", "限大學部",
               "限碩士班", "限研究生", "不開放初選", "限申請校學士", "碩博同修"):
        if kw in text and kw not in cond.other_conditions:
            cond.other_conditions.append(kw)

    if cond.min_grade or cond.max_grade or cond.allowed_depts or cond.prerequisites:
        cond.confidence = "medium"
    return cond


# ------------------------------------------------------------- AI 解析
_SYSTEM = (
    "你是協助解讀大學課程『限修條件』的助手。使用者會給你一段課程備註文字，"
    "請只萃取文字中明確提到的限修條件，輸出 JSON 物件，不要加任何說明或 Markdown。"
    "欄位：min_grade(整數年級下限或 null)、"
    "max_grade(整數年級上限或 null；例如『限二年級』為 min_grade=2、max_grade=2，"
    "『三年級以上』為 min_grade=3、max_grade=null)、allowed_depts(限定系所字串陣列)、"
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


def _dept_core(name: str) -> str:
    """系所名稱核心：去掉「系／所」字尾、簡稱換成正式名稱，再去掉結尾「學」。

    「化學學系」「化學系」→ 化學；「生科所」「生命科學系」→ 生命科；「數學系」→ 數學
    """
    core = re.sub(r"(?:系所|研究所|系|所)$", "", name.strip())
    core = _DEPT_ALIASES.get(core, core)
    while core.endswith("學") and len(core) > 2:       # 「大氣科學學」→「大氣科」
        core = core[:-1]
    return core


def _dept_match(user_dept: str, allowed: str) -> bool:
    """系所比對：簡稱與正式名稱視為相同（『資工系』＝『資訊工程學系』）。

    - 「非資訊管理學系」表示排除該系
    - 組別、班別視為該系（使用者『數學系』符合『數學系計算與資料科學組』）
    - 不做任意子字串比對，避免『化學系』誤配『化學工程與材料工程學系』
    """
    allowed = allowed.strip()
    if allowed.startswith("非"):
        return not _dept_match(user_dept, allowed[1:])
    u, a = _dept_core(user_dept), _dept_core(allowed)
    if not u or not a:
        return False
    return u == a or a.startswith(u + "系") or u.startswith(a + "系")


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
    lo, hi = condition.min_grade, condition.max_grade
    if lo is not None or hi is not None:
        if lo is not None and hi is not None:
            need = f"{lo}年級" if lo == hi else f"{lo}～{hi}年級"
        elif lo is not None:
            need = f"{lo}年級以上"
        else:
            need = f"{hi}年級以下"
        if user.grade is None:
            has_unknown = True
            reasons.append(f"限{need}，但未提供你的年級")
        elif (lo is None or user.grade >= lo) and (hi is None or user.grade <= hi):
            reasons.append(f"年級符合（限{need}，你是 {user.grade}）")
        else:
            hard_fail = True
            reasons.append(f"年級不符（限{need}，你是 {user.grade}）")

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
    """粗略判斷已修課是否涵蓋某先修課（去空白後互為子字串）。

    「A或B」只要修過其中一門即可。
    """
    b = re.sub(r"\s", "", done)
    if not b:
        return False
    for alt in prereq.split("或"):
        a = re.sub(r"\s", "", alt)
        if a and (a in b or b in a):
            return True
    return False


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


def _dedup(*lists: list[str]) -> list[str]:
    """合併多個字串清單並去重（保留先出現者）。"""
    out: list[str] = []
    for lst in lists:
        for x in lst:
            if x not in out:
                out.append(x)
    return out


def merge_conditions(notes_cond: EligibilityCondition,
                     dist_cond: EligibilityCondition) -> EligibilityCondition:
    """合併備註條件與分發條件（結構化）。分發條件較可靠，年級以其為優先。"""
    if dist_cond.is_empty():
        return notes_cond
    dist_has_grade = dist_cond.min_grade is not None or dist_cond.max_grade is not None
    grade_src = dist_cond if dist_has_grade else notes_cond
    return EligibilityCondition(
        min_grade=grade_src.min_grade,
        max_grade=grade_src.max_grade,
        allowed_depts=_dedup(dist_cond.allowed_depts, notes_cond.allowed_depts),
        prerequisites=_dedup(dist_cond.prerequisites, notes_cond.prerequisites),
        other_conditions=_dedup(dist_cond.other_conditions, notes_cond.other_conditions),
        unparsed=notes_cond.unparsed,
        confidence="high",            # 來自結構化分發條件
    )


def load_distribution(path: Path = DISTRIBUTION_CACHE_PATH
                      ) -> dict[str, EligibilityCondition]:
    """載入分發條件快取（課號 → 結構化條件）。檔案不存在回空 dict。"""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("conditions", {}) if isinstance(raw, dict) else {}
    out: dict[str, EligibilityCondition] = {}
    for cid, entry in items.items():
        cond_raw = entry.get("condition") if isinstance(entry, dict) else None
        c = _coerce_condition(cond_raw) if cond_raw is not None else None
        if c is not None:
            out[cid] = c
    return out


def evaluate(course_id: str, raw_notes: str, user: UserProfile,
             cache: dict[str, EligibilityCondition],
             distribution: dict[str, EligibilityCondition] | None = None
             ) -> EligibilityResult:
    """網頁使用：用快取的條件（不即時呼叫 AI）比對使用者，回傳判定結果。

    distribution 給定且含此課號時，與備註條件合併（結構化分發條件優先）。
    """
    cond = cache.get(course_id)
    if cond is None:
        cond = keyword_parse(raw_notes)  # 快取缺漏時的即時非 AI 退路
    if distribution:
        dist = distribution.get(course_id)
        if dist is not None:
            cond = merge_conditions(cond, dist)
    return match(cond, user, raw_notes)
