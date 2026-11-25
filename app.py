# -*- coding: utf-8 -*-
"""NCU 選課助理原型 — Flask 後端（階段 5）。

路由：
- GET  /                     主頁
- GET  /api/meta             節次/星期對照、資料來源（是否範例）
- GET  /api/courses          課程搜尋（q, dept, category, ge_domain）
- GET  /api/course/<id>      課程詳細（含原始備註與解析後限修條件）
- POST /api/free_slots       固定課號 → 空堂格 + 可填入候選課
- POST /api/parse_preferences 白話偏好 → 條件 JSON
- POST /api/schedules        使用者資料 + 固定課 + 條件 → 最多 3 組課表與推薦理由

原則：使用者資料只存瀏覽器，不在伺服器保存；AI 失敗一律 fallback，不讓網頁當掉。
埠號讀環境變數 PORT（預設 5001）。
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from planner.data_loader import load_courses, load_live_meta
from planner.models import Course, course_code_key
from planner.graduation import programs, load_demo, build_context
from planner.depts import student_depts
from planner import timeslots as ts
from planner.solver import (
    solve, compute_free_slots, candidate_courses, preference_score, Preferences,
)
from planner.eligibility import (
    load_cache, load_distribution, evaluate, keyword_parse, merge_conditions,
    UserProfile,
)
from planner.preferences import PreferenceSpec, parse_preferences, _coerce_spec
from planner.search import SemanticSearch, generate_reasons
from planner.llm_client import get_client

app = Flask(__name__)

# -------- 啟動時載入一次（原型；資料不大） --------
COURSES, DATA_SOURCE = load_courses()
DATA_META = load_live_meta() if DATA_SOURCE == "live" else {}
COURSE_BY_ID: dict[str, Course] = {c.id: c for c in COURSES}
ELIG_CACHE = load_cache()
DIST_CACHE = load_distribution()          # 結構化分發條件（比備註可靠，若有則合併）
SEARCH = SemanticSearch()
CLIENT = get_client()
IS_SAMPLE = DATA_SOURCE == "sample"
STUDENT_DEPTS = student_depts(COURSES)    # 學生系所（已排除行政單位、學院本身）
PROGRAM_DEPT = {p["id"]: p["department"] for p in programs()}


# ============================================================ 共用
def ok(payload: dict):
    payload = {"success": True, **payload}
    return jsonify(payload)


def bad(msg: str, code: int = 400):
    return jsonify({"success": False, "error": msg}), code


def course_brief(c: Course) -> dict:
    return {
        "id": c.id, "code": c.code, "name": c.name, "name_en": c.name_en,
        "teacher": c.teacher, "dept": c.dept, "credits": c.credits,
        "category": c.category, "ge_domain": c.ge_domain,
        "is_language": c.is_language, "language": c.language,
        "times": [[d, p] for d, p in c.times], "classroom": c.classroom,
        "capacity": c.capacity, "is_sample": c.is_sample,
    }


def planning_context(data, prefs):
    """選用畢業規則；不保存紀錄，也不把修課紀錄送入語言模型。"""
    context = build_context(data['graduation']) if data.get('graduation') is not None else None
    user = data.get('user') or {}
    if not isinstance(user, dict):
        raise ValueError('user 必須是物件')
    if user.get('dept') is not None and not isinstance(user['dept'], str):
        raise ValueError('dept 必須是字串')
    completed = user.get('completed_courses') or []
    if not isinstance(completed, list) or any(not isinstance(c, str) for c in completed):
        raise ValueError('completed_courses 必須是字串陣列')
    if context:
        program_dept = PROGRAM_DEPT.get(data['graduation'].get('program_id'))
        if user.get('dept') and user['dept'] != program_dept:
            raise ValueError(f'畢業規則與系所不一致；請選{program_dept}或關閉畢業規則')
        prefs.extra_course_codes = context.extra_candidate_codes(COURSES)
        prefs.excluded_course_codes |= context.blocked_codes
        prefs.max_courses_per_prefix = {'PE': 1}  # 每張建議課表最多一門體育，避免學分低而塞滿
        for course in COURSES:
            bits = context.contribution(course)['requirements']
            if '通識（領域及採認待查）' in bits:
                prefs.course_groups[course_code_key(course.code)] = 'general_education'
            elif '指定核心專題' in bits:
                prefs.course_groups[course_code_key(course.code)] = 'core_project'
        prefs.max_credits_per_group = {
            'general_education': context.report['common_required']['general_education']['remaining'],
            'core_project': context.report['core_electives']['core_project']['credits']['remaining'],
        }
    completed_set = {c.strip() for c in completed}
    for c in COURSES:
        if c.name in completed_set or course_code_key(c.code) in completed_set:
            prefs.excluded_course_codes.add(course_code_key(c.code))
    return context


def validate_fixed_ids(ids, context=None):
    if (not isinstance(ids, list) or len(ids) > 200
            or any(not isinstance(i, str) for i in ids)):
        raise ValueError('fixed_ids 必須是最多200筆流水號的字串陣列')
    if any(i not in COURSE_BY_ID for i in ids):
        raise ValueError('固定課已不在目前課程資料，請重新搜尋加入')
    if context:
        passed = {c['course_id'] for c in context.student['courses'] if c['status']=='passed'}
        if any(course_code_key(COURSE_BY_ID[i].code) in passed for i in ids):
            raise ValueError('固定課含已完成課程，請先移除；重修須先更新修課紀錄')
    fixed = [COURSE_BY_ID[i] for i in dict.fromkeys(ids)]
    codes = [course_code_key(c.code) for c in fixed]
    if len(codes) != len(set(codes)):
        raise ValueError('固定課包含同一課號多個班次，請只選一班')
    if ts.any_conflict(fixed):
        raise ValueError('固定課彼此衝堂，請先調整固定課')


# ============================================================ 頁面
@app.get("/")
def index():
    return render_template("index.html", is_sample=IS_SAMPLE,
                           data_source=DATA_SOURCE)


@app.get("/api/meta")
def meta():
    return ok({
        "data_source": DATA_SOURCE,
        "is_sample": IS_SAMPLE,
        "course_count": len(COURSES),
        "semester": DATA_META.get("semester"),
        "semester_label": DATA_META.get("semester_label"),
        "data_updated": DATA_META.get("updated"),
        "semantic_available": SEARCH.available,
        "llm_provider": CLIENT.provider,
        "weekdays": ts.WEEKDAY_NAMES,
        "period_order": ts.PERIOD_ORDER,
        "period_times": ts.PERIOD_TIMES,
        "default_days": ts.DEFAULT_DAYS,
        "categories": sorted({c.category for c in COURSES if c.category}),
        "ge_domains": sorted({c.ge_domain for c in COURSES if c.ge_domain}),
        "languages": sorted({c.language for c in COURSES if c.language}),
        "dept_groups": STUDENT_DEPTS,
        "graduation_programs": programs(),
    })


@app.get('/api/graduation/demo/<program_id>')
def graduation_demo(program_id):
    try:
        return ok({'student': load_demo(program_id)})
    except ValueError as exc:
        return bad(str(exc), 404)


@app.post('/api/graduation/check')
def graduation_check():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return bad('請提供program_id與student的JSON物件')
    try:
        context = build_context(data)
    except ValueError as exc:
        return bad(str(exc))
    return ok({'report': context.describe(COURSES, DATA_META.get('semester'), IS_SAMPLE)})


# ============================================================ 課程搜尋
@app.get("/api/courses")
def api_courses():
    q = (request.args.get("q") or "").strip()
    dept = (request.args.get("dept") or "").strip()
    category = (request.args.get("category") or "").strip()
    ge_domain = (request.args.get("ge_domain") or "").strip()
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        return bad("limit 必須是整數")

    results = []
    for c in COURSES:
        if q and q.lower() not in (
            f"{c.name} {c.name_en or ''} {c.code} {' '.join(c.teacher)}".lower()
        ):
            continue
        if dept and dept not in c.dept:
            continue
        if category and c.category != category:
            continue
        if ge_domain and (c.ge_domain or "") != ge_domain:
            continue
        results.append(course_brief(c))
        if len(results) >= limit:
            break
    return ok({"count": len(results), "courses": results})


@app.get("/api/course/<cid>")
def api_course(cid: str):
    c = COURSE_BY_ID.get(cid)
    if c is None:
        return bad("找不到此課程", 404)
    cond = ELIG_CACHE.get(cid) or (keyword_parse(c.notes) if c.notes else None)
    dist = DIST_CACHE.get(cid)
    if dist is not None:
        cond = merge_conditions(cond, dist) if cond is not None else dist
    return ok({
        "course": course_brief(c),
        "syllabus_url": c.syllabus_url,
        "notes": c.notes,
        "eligibility_condition": cond.to_dict() if cond else None,
    })


# ============================================================ 空堂 + 候選
@app.post("/api/free_slots")
def api_free_slots():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return bad('請提供JSON物件')
    fixed_ids = data.get("fixed_ids")
    if not isinstance(fixed_ids, list):
        return bad("fixed_ids 必須是陣列")
    try:
        spec = _coerce_spec(data.get("preferences") or {}, strict=True)
        prefs = spec.to_solver_prefs()
        context = planning_context(data, prefs)
        validate_fixed_ids(fixed_ids, context)
    except ValueError as exc:
        return bad(str(exc))

    fixed = [COURSE_BY_ID[i] for i in fixed_ids if i in COURSE_BY_ID]
    free = compute_free_slots(COURSES, [c.id for c in fixed], prefs)
    cands = candidate_courses(COURSES, fixed, free, prefs)
    return ok({
        "free_slots": sorted([[d, p] for d, p in free]),
        "fixed": [course_brief(c) for c in fixed],
        "candidate_count": len(cands),
        "candidates": [course_brief(c) for c in cands[:100]],
    })


# ============================================================ 白話偏好解析
@app.post("/api/parse_preferences")
def api_parse_preferences():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return bad('請提供JSON物件')
    text = data.get("text")
    if not isinstance(text, str):
        return bad("text 必須是字串")
    spec = parse_preferences(text, CLIENT)
    return ok({"preferences": spec.to_dict()})


# ============================================================ 課表組合
@app.post("/api/schedules")
def api_schedules():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return bad('請提供JSON物件')
    fixed_ids = data.get("fixed_ids")
    if not isinstance(fixed_ids, list):
        return bad("fixed_ids 必須是陣列")

    user_raw = data.get("user") or {}
    if not isinstance(user_raw, dict):
        return bad("user 必須是物件")
    completed = user_raw.get('completed_courses') or []
    if not isinstance(completed, list) or any(not isinstance(c, str) for c in completed):
        return bad('completed_courses 必須是字串陣列')
    grade = user_raw.get("grade")
    try:
        grade = int(grade) if grade not in (None, "") else None
    except (ValueError, TypeError):
        return bad("grade 必須是整數")
    user = UserProfile(
        dept=(user_raw.get("dept") or None),
        grade=grade,
        completed_courses=[s for s in completed if s],
    )

    max_sched = data.get("max_schedules", 3)
    if type(max_sched) is not int or not 1 <= max_sched <= 5:
        return bad("max_schedules 必須是 1～5 的整數")
    try:
        spec = _coerce_spec(data.get("preferences") or {}, strict=True)
        prefs = spec.to_solver_prefs()
        context = planning_context(data, prefs)
        validate_fixed_ids(fixed_ids, context)
    except ValueError as exc:
        return bad(str(exc))
    if context:
        user.completed_courses = sorted(set(user.completed_courses + context.passed_names_and_codes))

    score_fn = SEARCH.build_score_fn(spec.topics)
    if context:
        semantic_score = score_fn
        score_fn = lambda c: semantic_score(c) + context.contribution(c)['priority']
    # 使用者未設學分上限時，限制最多加入的通識/語言課門數，避免把整週空堂塞滿
    # （分數隨課數遞增，否則會排出 30–40 學分的誇張課表）。
    default_added_cap = 6
    max_added = None if spec.max_credits is not None else default_added_cap
    schedules, stats = solve(COURSES, fixed_ids, prefs, score_fn=score_fn,
                             max_schedules=max_sched, max_added=max_added,
                             max_zero_credit_added=0 if any(
                                 course_code_key(COURSE_BY_ID[i].code).startswith('PE')
                                 for i in fixed_ids) else 1)

    smap = SEARCH.score_map(" ".join(spec.topics)) if spec.topics else {}
    free = compute_free_slots(COURSES, fixed_ids, prefs)

    # 供「同時段其他可選」用：所有可填入的通識/語言候選課與其排序分數
    fixed_courses = [COURSE_BY_ID[i] for i in fixed_ids if i in COURSE_BY_ID]
    cands_all = candidate_courses(COURSES, fixed_courses, free, prefs)

    def alt_score(c: Course) -> float:
        return preference_score(c, prefs) + smap.get(c.id, 0.0)

    def alternatives_for(course: Course, sched_ids: set[str],
                         occ_added: set) -> list[dict]:
        """同時段可替換的其他候選課：移除此課後仍放得下、不與其他課衝堂、符合篩選。"""
        allowed = free - (occ_added - set(course.times))   # 空堂扣掉「其他推薦課」佔用
        alts = [d for d in cands_all
                if d.id not in sched_ids and set(d.times).issubset(allowed)]
        alts.sort(key=lambda d: (-alt_score(d), d.id))
        out_alts = []
        for d in alts[:6]:
            elig = evaluate(d.id, d.notes, user, ELIG_CACHE, DIST_CACHE)
            out_alts.append({**course_brief(d), "eligibility_status": elig.status,
                             "graduation": context.contribution(d) if context else None})
        return out_alts

    # 各方案的推薦課去重後一次產生理由（同一門課只問一次 LLM）
    reasons = generate_reasons([c for s in schedules for c in s.added],
                               spec.topics, CLIENT, score_map=smap)
    out = []
    for s in schedules:
        sched_ids = {c.id for c in s.courses}
        occ_added = set().union(*[set(a.times) for a in s.added]) if s.added else set()
        courses_out = []
        for c in s.courses:
            is_fixed = any(fc.id == c.id for fc in s.fixed)
            elig = evaluate(c.id, c.notes, user, ELIG_CACHE, DIST_CACHE)
            entry = {
                **course_brief(c),
                "role": "fixed" if is_fixed else "added",
                "reason": reasons.get(c.id) if not is_fixed else None,
                "eligibility": elig.to_dict(),
                "graduation": context.contribution(c) if context else None,
            }
            if not is_fixed:
                entry["alternatives"] = alternatives_for(c, sched_ids, occ_added)
            courses_out.append(entry)
        out.append({
            "total_credits": s.total_credits,
            "score": round(s.score, 3),
            "courses": courses_out,
        })

    return ok({
        "schedules": out,
        "free_slots": sorted([[d, p] for d, p in free]),
        "stats": {"nodes": stats.nodes, "elapsed": round(stats.elapsed, 3),
                  "hit_limit": stats.hit_limit, "added_cap": max_added},
        "preferences_used": spec.to_dict(),
        "graduation_report": context.describe(COURSES, DATA_META.get('semester'), IS_SAMPLE) if context else None,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    # 預設只聽本機、關閉除錯器：Werkzeug 除錯器可遠端執行程式碼，不可對區網開放。
    # 需要讓同網段裝置連線（如 Demo 用手機開）時設 HOST=0.0.0.0；開發時要自動重載設 FLASK_DEBUG=1。
    host = os.environ.get("HOST", "127.0.0.1")
    debug = os.environ.get("FLASK_DEBUG") == "1"
    if debug and host not in ("127.0.0.1", "localhost"):
        print("！FLASK_DEBUG=1 只能搭配本機 HOST，已關閉除錯模式")
        debug = False
    print(f"資料來源：{DATA_SOURCE}（{len(COURSES)} 門）｜語意索引：{SEARCH.available}"
          f"｜LLM：{CLIENT.provider}")
    app.run(host=host, port=port, debug=debug)
