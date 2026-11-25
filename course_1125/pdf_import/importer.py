"""文字型中央大學主系審核PDF → 去識別化課程紀錄。

以PROGRAM_REGISTRY.json確認系所、組別及年度，只匯入10000主系。
不執行畢業資格判定，不輸出姓名、學號、分數，不自動套用案例假設。
"""
from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
HEADER = re.compile(r"^\s*(\d{3})-\d+-\d+-\d+-(.+?)\s*$", re.M)
MAIN = re.compile(r"^\s*10000\s*-\s*主系", re.M)
OTHER = re.compile(r"^\s*[2-9]0000\s*-", re.M)
CATEGORY = re.compile(r"^\s*(?:完成|未完成)\s+(\d{5})\s*-")
COURSE = re.compile(r"^\s*(\d{4})\s+([A-Z]{2}\d{4})\s+(.+?)\s+(\d+(?:\.\d+)?)\s+(.+)$")
# 網站規劃用id與全校資料建檔id分開；此處只提供明確的相容別名。
PROGRAM_ALIASES = {"math_datascience_113": "math_datascience_113_official"}


def compact(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def resolve_program(department: str, entry_year_roc: int, program_id: str | None = None) -> dict:
    """同系多組別未指明時拒絕猜測；指定id也必須符合來源系所、組別、年度。"""
    programs = json.loads((ROOT / "PROGRAM_REGISTRY.json").read_text(encoding="utf-8"))["programs"]
    name = compact(department)
    base = re.split(r"[（(]", name, maxsplit=1)[0]
    candidates = [p for p in programs
                  if p["academic_year_roc"] == entry_year_roc
                  and compact(p["department"]) == base]
    if not candidates:
        raise ValueError("找不到符合主系與入學年度的規則；不套用其他系所或年度")
    track = None
    match = re.search(r"[（(](.*?)[）)]", name)
    if match:
        track = compact(match[1])
    if program_id is not None:
        canonical = PROGRAM_ALIASES.get(program_id, program_id)
        selected = next((p for p in candidates if p["program_id"] == canonical), None)
        if selected is None or (track and compact(selected.get("track") or "") != track):
            raise ValueError("指定program_id與PDF主系、組別或年度不符")
        return dict(selected)
    if track:
        candidates = [p for p in candidates if compact(p.get("track") or "") == track]
    if len(candidates) != 1:
        raise ValueError("主系有多個組別或無法確認組別，請明確指定--program-id")
    return dict(candidates[0])


def detect_status(text: str) -> str:
    value = compact(text).lower()
    if any(token in value for token in ("未到", "notentered", "成績未登錄")):
        return "in_progress"
    if any(token in value for token in ("停修", "退選", "withdrawn")):
        return "withdrawn"
    if any(token in value for token in ("未通過", "不及格", "failed")):
        return "failed"
    if "通過" in value or re.search(r"\bpassed?\b", value):
        return "passed"
    return "needs_review"


def parse_text(text: str, program_id: str | None = None) -> dict:
    main = MAIN.search(text)
    if main is None:
        raise ValueError("找不到10000 - 主系；請使用文字型畢業審核紀錄表")
    headers = list(HEADER.finditer(text[:main.start()]))
    if not headers:
        raise ValueError("無法確認主系與入學年度")
    year, department = headers[-1].groups()
    program = resolve_program(department, int(year), program_id)
    body = text[main.end():]
    boundary = OTHER.search(body)
    if boundary:
        body = body[:boundary.start()]
    category = None
    rows = {}
    for line in body.splitlines():
        rule = CATEGORY.match(line)
        if rule:
            category = rule[1]
        match = COURSE.match(line)
        if not match:
            continue
        term, code, name, credit, rest = match.groups()
        status = detect_status(rest)
        if status == "needs_review":
            raise ValueError(f"{code}狀態不明，請人工確認後再匯入")
        key = (term, code)
        if key in rows:
            previous = rows[key]
            if previous["status"] != status or previous["credits"] != float(credit):
                raise ValueError(f"{code}同學期紀錄衝突，請人工核對")
            if category and category not in previous["listed_under_rule_codes"]:
                previous["listed_under_rule_codes"].append(category)
            continue
        rows[key] = {
            "term": term, "course_id": code, "name": name.strip(),
            "credits": float(credit), "status": status,
            "source_rule_code": category,
            "listed_under_rule_codes": [category] if category else [],
        }
    if not rows:
        raise ValueError("主系段落沒有可解析課程；不支援掃描影像PDF")
    return {
        "document_type": "ncu_main_major_audit_import",
        "source_type": "university_graduation_audit_pdf",
        "program_id": program["program_id"],
        "entry_year_roc": int(year), "department": department.strip(),
        "program": program.get("track"),
        "student_record_is_anonymized": True,
        "other_programs_excluded": True,
        "double_major_excluded": True,
        "courses": list(rows.values()),
        "warnings": [
            "此為去識別化修課紀錄，不是畢業資格審核；未知採認不自動計入畢業學分。",
            "重修跨學期紀錄保留，後續須由對應系所的規則引擎核對採認。",
        ],
    }


def parse_pdf(path: str | Path, program_id: str | None = None) -> dict:
    with fitz.open(path) as document:
        text = "\n".join(page.get_text(sort=True) for page in document)
    return parse_text(text, program_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf")
    parser.add_argument("--program-id")
    parser.add_argument("-o", "--output", default="main_major_courses.json")
    args = parser.parse_args()
    result = parse_pdf(args.pdf, args.program_id)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(f"已匯入{len(result['courses'])}筆匿名主系紀錄：{args.output}")


if __name__ == "__main__":
    main()
