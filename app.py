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

from planner.data_loader import load_courses
from planner.models import Course
from planner import timeslots as ts
from planner.solver import solve, compute_free_slots, candidate_courses, Preferences
from planner.eligibility import load_cache, evaluate, keyword_parse, UserProfile
from planner.preferences import PreferenceSpec, parse_preferences, _coerce_spec
from planner.search import SemanticSearch, generate_reasons
from planner.llm_client import get_client

app = Flask(__name__)

# -------- 啟動時載入一次（原型；資料不大） --------
COURSES, DATA_SOURCE = load_courses()
COURSE_BY_ID: dict[str, Course] = {c.id: c for c in COURSES}
ELIG_CACHE = load_cache()
SEARCH = SemanticSearch()
CLIENT = get_client()
IS_SAMPLE = DATA_SOURCE == "sample"


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
        "semantic_available": SEARCH.available,
        "llm_provider": CLIENT.provider,
        "weekdays": ts.WEEKDAY_NAMES,
        "period_order": ts.PERIOD_ORDER,
        "period_times": ts.PERIOD_TIMES,
        "default_days": ts.DEFAULT_DAYS,
        "ge_domains": sorted({c.ge_domain for c in COURSES if c.ge_domain}),
        "languages": sorted({c.language for c in COURSES if c.language}),
        "depts": sorted({c.dept for c in COURSES if c.dept and c.dept != "（未分類）"}),
    })


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
    fixed_ids = data.get("fixed_ids")
    if not isinstance(fixed_ids, list):
        return bad("fixed_ids 必須是陣列")
    spec = _coerce_spec(data.get("preferences") or {}) or PreferenceSpec()
    prefs = spec.to_solver_prefs()

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
    text = data.get("text")
    if not isinstance(text, str):
        return bad("text 必須是字串")
    spec = parse_preferences(text, CLIENT)
    return ok({"preferences": spec.to_dict()})


# ============================================================ 課表組合
@app.post("/api/schedules")
def api_schedules():
    data = request.get_json(silent=True) or {}
    fixed_ids = data.get("fixed_ids")
    if not isinstance(fixed_ids, list):
        return bad("fixed_ids 必須是陣列")

    user_raw = data.get("user") or {}
    if not isinstance(user_raw, dict):
        return bad("user 必須是物件")
    grade = user_raw.get("grade")
    try:
        grade = int(grade) if grade not in (None, "") else None
    except (ValueError, TypeError):
        return bad("grade 必須是整數")
    user = UserProfile(
        dept=(user_raw.get("dept") or None),
        grade=grade,
        completed_courses=[s for s in (user_raw.get("completed_courses") or []) if s],
    )

    spec = _coerce_spec(data.get("preferences") or {}) or PreferenceSpec()
    prefs = spec.to_solver_prefs()
    try:
        max_sched = min(int(data.get("max_schedules", 3)), 5)
    except (ValueError, TypeError):
        max_sched = 3

    score_fn = SEARCH.build_score_fn(spec.topics)
    schedules, stats = solve(COURSES, fixed_ids, prefs, score_fn=score_fn,
                             max_schedules=max_sched)

    smap = SEARCH.score_map(" ".join(spec.topics)) if spec.topics else {}
    free = compute_free_slots(COURSES, fixed_ids, prefs)

    out = []
    for s in schedules:
        reasons = generate_reasons(s.added, spec.topics, CLIENT, score_map=smap)
        courses_out = []
        for c in s.courses:
            is_fixed = any(fc.id == c.id for fc in s.fixed)
            elig = evaluate(c.id, c.notes, user, ELIG_CACHE)
            courses_out.append({
                **course_brief(c),
                "role": "fixed" if is_fixed else "added",
                "reason": reasons.get(c.id) if not is_fixed else None,
                "eligibility": elig.to_dict(),
            })
        out.append({
            "total_credits": s.total_credits,
            "score": round(s.score, 3),
            "courses": courses_out,
        })

    return ok({
        "schedules": out,
        "free_slots": sorted([[d, p] for d, p in free]),
        "stats": {"nodes": stats.nodes, "elapsed": round(stats.elapsed, 3),
                  "hit_limit": stats.hit_limit},
        "preferences_used": spec.to_dict(),
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"資料來源：{DATA_SOURCE}（{len(COURSES)} 門）｜語意索引：{SEARCH.available}"
          f"｜LLM：{CLIENT.provider}")
    app.run(host="0.0.0.0", port=port, debug=True)
