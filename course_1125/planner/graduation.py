"""數學系計算與資料科學組113入學：可追溯、保守的畢業缺口檢查。"""

import json
import copy
import math
import re
from dataclasses import dataclass
from .models import Course, course_code_key
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "data" / "graduation"
VALID = {"passed", "in_progress", "failed", "withdrawn"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def progress(required, done):
    return {
        "required": required,
        "completed": done,
        "remaining": max(0, required - done),
    }


def _evaluate_records(rules, student):
    a = rules["applicability"]
    if (
        student.get("department") != a["department"]
        or student.get("entry_year_roc") != a["admission_academic_year_roc"]
    ):
        raise ValueError("僅支援113入學數學系(計算與資料科學組)主系")
    if not student.get("other_programs_excluded"):
        raise ValueError("尚未確認排除其他學程")
    rows = student["courses"]
    seen = set()
    for c in rows:
        key = (c["term"], c["course_id"])
        if (
            key in seen
            or c["status"] not in VALID
            or not isinstance(c["credits"], (int, float))
            or c["credits"] < 0
        ):
            raise ValueError("重複紀錄、未知狀態或學分不合法")
        seen.add(key)
    # 有學分課依課號採通過優先；操行、體育、服務學習仍逐學期計次。
    passed = {}
    for c in rows:
        if c["status"] == "passed":
            if (
                c["course_id"] in passed
                and c["credits"] != passed[c["course_id"]]["credits"]
            ):
                raise ValueError("重修學分不一致，需人工核對")
            passed[c["course_id"]] = c
    required = rules["department_required"]["fixed_courses"]
    fixed_done = sum(c["credits"] for c in required if c["course_id"] in passed)
    fixed = {
        "credits": progress(47, fixed_done),
        "missing_courses": [c for c in required if c["course_id"] not in passed],
    }

    def category_sum(codes):
        return sum(
            c["credits"] for c in passed.values() if c["source_rule_code"] in codes
        )

    foreign = category_sum({"11201", "11204", "11205"})
    chinese = category_sum({"11401"})
    ge = category_sum({"11321", "11322", "11323", "11324", "11325"})
    common = {
        "foreign_language": progress(6, min(6, foreign)),
        "chinese": progress(5, min(5, chinese)),
        "general_education": progress(14, min(14, ge)),
        "area_distribution": "待查",
    }
    cores = {}
    for g in rules["core_electives"]["choice_groups"]:
        eligible = {
            c["course_id"]: c
            for c in rows
            if c["course_id"] in g["known_course_ids"]
            or c["source_rule_code"] == g["source_rule_code"]
        }
        done = [code for code in eligible if code in passed]
        cores[g["id"]] = {
            "courses": progress(g["required_course_count"], len(done)),
            "credits": progress(
                g["required_credits"],
                min(g["required_credits"], sum(passed[x]["credits"] for x in done)),
            ),
            "passed_course_ids": done,
            "in_progress_course_ids": [
                code
                for code, c in eligible.items()
                if code not in passed and c["status"] == "in_progress"
            ],
            "known_options": g["known_course_ids"],
            "option_catalog_complete": g["option_catalog_complete"],
        }
    natural = []
    for g in rules["natural_science"]["series"]:
        done = [
            c
            for c in passed.values()
            if c["course_id"] in g["known_course_ids"]
            or c["source_rule_code"] == g["source_rule_code"]
        ]
        natural.append(
            {
                "id": g["id"],
                "credits": progress(6, sum(c["credits"] for c in done)),
                "courses": progress(2, len(done)),
                "missing_known_course_ids": [
                    x for x in g["known_course_ids"] if x not in passed
                ],
            }
        )
    dep = sum(
        c["credits"]
        for c in passed.values()
        if c.get("department_elective_eligible") is True
    )
    recognized = sum(
        c["credits"]
        for c in passed.values()
        if c.get("graduation_credit_eligible") is True
    )
    # 通識超修不能增加畢業總學分。
    recognized_ge = sum(
        c["credits"]
        for c in passed.values()
        if c.get("graduation_credit_eligible") is True
        and c["source_rule_code"] in {"11321", "11322", "11323", "11324", "11325"}
    )
    recognized -= max(0, recognized_ge - 14)
    nc = {}
    for k, cat, target in [
        ("freshman_pe", "18201", 2),
        ("other_pe", "18202", 3),
        ("service_learning", "18401", 2),
        ("conduct", "18101", 8),
    ]:
        nc[k] = progress(
            target,
            sum(c["status"] == "passed" and c["source_rule_code"] == cat for c in rows),
        )
    nc["learning_passport"] = {
        "hours": student.get("learning_passport_hours"),
        "required_hours": None,
        "status": student.get("audit_categories", {})
        .get("18500", {})
        .get("status", "unknown"),
    }
    nc["english_proficiency"] = {
        "status": student.get("audit_categories", {})
        .get("19200", {})
        .get("status", "unknown")
    }
    return {
        "rules_id": rules["rules_id"],
        "student_label": student.get("label"),
        "input_mode": student.get("source_mode"),
        "total_credits": progress(128, recognized),
        "passed_credits_pending_recognition": sum(
            c["credits"]
            for c in passed.values()
            if c.get("graduation_credit_eligible") is not True
        ),
        "department_required": fixed,
        "common_required": common,
        "core_electives": cores,
        "natural_science": {
            "series": natural,
            "completed": any(
                g["credits"]["remaining"] == 0 and g["courses"]["remaining"] == 0
                for g in natural
            ),
        },
        "department_electives": {
            **progress(21, dep),
            "overlap_policy": "核心與系所選修的重疊採認待查，不加總缺額",
        },
        "noncredit_requirements": nc,
        "in_progress_credits": sum(
            c["credits"] for c in rows if c["status"] == "in_progress"
        ),
        "planning_overrides": student.get("planning_overrides", []),
        "to_verify": rules["unresolved"],
        "graduation_verdict": "初步缺口檢查；實際畢業資格仍待學校正式審核",
    }


PROGRAM_ID = "math_datascience_113"
PROGRAM_LABEL = "數學系・計算與資料科學組・113入學"
CATEGORY_CODES = {
    "11201",
    "11204",
    "11205",
    "11321",
    "11322",
    "11323",
    "11324",
    "11325",
    "11401",
    "12101",
    "13101",
    "13102",
    "13201",
    "13202",
    "13203",
    "14101",
    "18101",
    "18201",
    "18202",
    "18401",
}


def programs() -> list[dict]:
    return [
        {
            "id": PROGRAM_ID,
            "label": PROGRAM_LABEL,
            "entry_year_roc": 113,
            "department": "數學系",
            "program": "計算與資料科學組",
        }
    ]


def load_rules(program_id: str) -> dict:
    if program_id != PROGRAM_ID:
        raise ValueError("尚未支援這個系所／組別／入學年度")
    return read_json(BASE / "math_datascience_113_rules.json")


def load_demo(program_id: str) -> dict:
    load_rules(program_id)
    return read_json(BASE / "math_datascience_113_demo.json")


def validate_student(rules: dict, raw: dict) -> dict:
    """拒絕錯年度、錯組別及不明狀態；不替使用者推定課程通過或採認。"""
    if not isinstance(raw, dict):
        raise ValueError("student 必須是修課紀錄JSON物件")
    scope = rules["applicability"]
    if (
        type(raw.get("entry_year_roc")) is not int
        or raw["entry_year_roc"] != scope["admission_academic_year_roc"]
        or raw.get("department") != scope["department"]
        or raw.get("program") != scope["program"]
    ):
        raise ValueError("此規則只適用113入學、數學系計算與資料科學組")
    if raw.get("other_programs_excluded") is not True:
        raise ValueError("請先排除師培、雙主修及其他學程")
    rows = raw.get("courses")
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("courses 必須是最多1000筆紀錄的陣列")
    student = copy.deepcopy(raw)
    for row in student["courses"]:
        if not isinstance(row, dict):
            raise ValueError("每筆修課紀錄必須是物件")
        code = row.get("course_id")
        term = row.get("term")
        if (
            not isinstance(code, str)
            or not re.fullmatch(r"[A-Z]{2}\d{4}", code)
            or not isinstance(term, str)
            or not re.fullmatch(r"\d{4}", term)
        ):
            raise ValueError(
                "修課紀錄須提供六碼course_id（如MA2011）與四碼term（如1151）"
            )
        credits = row.get("credits")
        if (
            type(credits) not in (int, float)
            or not math.isfinite(credits)
            or not 0 <= credits <= 30
        ):
            raise ValueError("credits 必須是0至30的有限數值")
        if not isinstance(row.get("status"), str) or row["status"] not in VALID:
            raise ValueError("status 必須是passed / in_progress / failed / withdrawn")
        if (
            not isinstance(row.get("source_rule_code"), str)
            or row["source_rule_code"] not in CATEGORY_CODES
        ):
            raise ValueError("缺少有效的source_rule_code；請用主系JSON格式匯入")
        if not isinstance(row.get("name"), str) or len(row["name"]) > 200:
            raise ValueError("name 必須是最多200字的課名")
        for field in ("graduation_credit_eligible", "department_elective_eligible"):
            if row.get(field) is not None and type(row[field]) is not bool:
                raise ValueError(f"{field} 只能是true / false / null")
    if not isinstance(student.get("audit_categories", {}), dict):
        raise ValueError("audit_categories 必須是物件")
    for category in student.get("audit_categories", {}).values():
        if not isinstance(category, dict):
            raise ValueError("審核分類快照必須是物件")
    hours = student.get("learning_passport_hours")
    if hours is not None and (
        type(hours) not in (int, float) or not math.isfinite(hours) or hours < 0
    ):
        raise ValueError("learning_passport_hours 必須是非負數或null")
    return student


def requirement_rows(report: dict) -> list[dict]:
    """前端使用的統一格式；各列是不同檢核視角，不相加。"""
    rows = []

    def add(key, label, values, unit="學分"):
        rows.append({"id": key, "label": label, "unit": unit, **values})

    add("total", "畢業總學分", report["total_credits"])
    add("required", "系訂必修", report["department_required"]["credits"])
    for key, label in [
        ("foreign_language", "外文"),
        ("chinese", "國文"),
        ("general_education", "通識"),
    ]:
        add(key, label, report["common_required"][key])
    for key, label in [
        ("core_intro", "核心入門選修"),
        ("core_project", "指定核心專題"),
    ]:
        add(key, label, report["core_electives"][key]["credits"])
    best = max(
        report["natural_science"]["series"], key=lambda s: s["credits"]["completed"]
    )
    add("science", "自然科學完整系列（不可混搭）", best["credits"])
    add(
        "department_electives",
        "系所選修（採認待核對）",
        {
            key: report["department_electives"][key]
            for key in ("required", "completed", "remaining")
        },
    )
    for key, label, unit in [
        ("freshman_pe", "大一體育", "門"),
        ("other_pe", "其他年級體育", "門"),
        ("service_learning", "服務學習", "門"),
        ("conduct", "操行", "學期"),
    ]:
        add(key, label, report["noncredit_requirements"][key], unit)
    return rows


@dataclass
class GraduationContext:
    rules: dict
    student: dict
    report: dict

    @property
    def blocked_codes(self) -> set[str]:
        return {
            c["course_id"]
            for c in self.student["courses"]
            if c["status"] in {"passed", "in_progress"}
        }

    @property
    def passed_names_and_codes(self) -> list[str]:
        return sorted(
            {
                text
                for c in self.student["courses"]
                if c["status"] == "passed"
                for text in (c["course_id"], c["name"])
            }
        )

    def contribution(self, course: Course) -> dict:
        """候選課可協助的條件；不將推薦課或在修課寫成已完成。"""
        code = course_code_key(course.code)
        bits = []
        pending = {
            c["course_id"]
            for c in self.student["courses"]
            if c["status"] == "in_progress"
        }
        missing_fixed = {
            c["course_id"]
            for c in self.report["department_required"]["missing_courses"]
        }
        if code in missing_fixed:
            bits.append("系訂必修")
        for group in self.rules["core_electives"]["choice_groups"]:
            state = self.report["core_electives"][group["id"]]
            pending_count = len(set(group["known_course_ids"]) & pending)
            if (
                state["courses"]["remaining"] > pending_count
                and code in group["known_course_ids"]
            ):
                bits.append(
                    "指定核心專題" if group["id"] == "core_project" else "核心入門選修"
                )
        series = self.report["natural_science"]["series"]
        best = max(series, key=lambda g: g["credits"]["completed"])
        if (
            not self.report["natural_science"]["completed"]
            and code in best["missing_known_course_ids"]
        ):
            bits.append("自然科學完整系列")
        if (
            self.report["common_required"]["general_education"]["remaining"] > 0
            and course.ge_domain
            and not course.is_language
        ):
            bits.append("通識（領域及採認待查）")
        if (
            re.fullmatch(r"MA[1-4]\d{3}", code)
            and not bits
            and course.category == "選修"
            and course.credits > 0
            and self.report["department_electives"]["remaining"] > 0
        ):
            bits.append("系所選修（採認待查）")
        if (
            code.startswith("PE")
            and not code.startswith(("PE10",))
            and "隊" not in course.name
            and self.report["noncredit_requirements"]["other_pe"]["remaining"] > 0
        ):
            bits.append("其他年級體育")
        return {
            "requirements": bits,
            "note": "通過並經學校採認後才計入；各條件可能重疊，不直接加總學分",
            "priority": (
                12
                if any(
                    b in bits for b in ("系訂必修", "指定核心專題", "自然科學完整系列")
                )
                else 6 if bits else 0
            ),
        }

    def extra_candidate_codes(self, courses: list[Course]) -> set[str]:
        return {
            course_code_key(c.code)
            for c in courses
            if course_code_key(c.code) not in self.blocked_codes
            and self.contribution(c)["requirements"]
        }

    def describe(
        self,
        courses: list[Course],
        semester: str | None = None,
        is_sample: bool = False,
    ) -> dict:
        available = {course_code_key(c.code) for c in courses}
        needed = set()
        needed.update(
            c["course_id"]
            for c in self.report["department_required"]["missing_courses"]
        )
        for group in self.rules["core_electives"]["choice_groups"]:
            if self.report["core_electives"][group["id"]]["courses"]["remaining"]:
                needed.update(group["known_course_ids"])
        best = max(
            self.report["natural_science"]["series"],
            key=lambda g: g["credits"]["completed"],
        )
        if not self.report["natural_science"]["completed"]:
            needed.update(best["missing_known_course_ids"])
        missing = sorted(needed - self.blocked_codes - available)
        hints = []
        if semester:
            hints.append(
                f"課程清單是{semester}，只代表這個資料學期；未推定115-2或其他學期開課。"
            )
        if is_sample:
            hints.append("目前為虛構範例課程，不能作為實際開課證據。")
        hints.append("在修課不算完成，亦不會再次自動推薦；需確認未通過才安排重修。")
        return {
            **self.report,
            "requirement_rows": requirement_rows(self.report),
            "program_id": PROGRAM_ID,
            "program_label": PROGRAM_LABEL,
            "available_semester": semester,
            "course_data_is_sample": is_sample,
            "unavailable_needed_course_codes": missing,
            "excluded_course_codes": sorted(self.blocked_codes),
            "data_notes": hints,
        }


def build_context(payload: dict) -> GraduationContext:
    if not isinstance(payload, dict):
        raise ValueError("graduation 必須包含program_id與student物件")
    rules = load_rules(payload.get("program_id"))
    student = validate_student(rules, payload.get("student"))
    report = _evaluate_records(rules, student)
    return GraduationContext(rules, student, report)
