"""113數學系規則、77學分案例與實際排課串接，皆為離線測試。"""

import copy
import math

import pytest

from planner.graduation import PROGRAM_ID, build_context, load_demo, load_rules
from planner.models import Course, course_code_key
from planner.solver import Preferences, solve
from planner import timeslots as ts


def payload():
    return {"program_id": PROGRAM_ID, "student": load_demo(PROGRAM_ID)}


def course(cid, code, day=1, credits=3, category="選修", **extra):
    return Course(
        id=cid,
        code=code,
        name=code,
        teacher=[],
        dept="數學系",
        credits=credits,
        times=[(day, "2")],
        category=category,
        **extra,
    )


def test_77_credit_case_and_separate_requirements():
    context = build_context(payload())
    report = context.describe([], "1151")
    assert report["total_credits"] == {
        "required": 128,
        "completed": 77,
        "remaining": 51,
    }
    assert report["department_required"]["credits"]["remaining"] == 0
    assert report["common_required"]["general_education"]["remaining"] == 4
    assert report["core_electives"]["core_intro"]["credits"]["remaining"] == 6
    assert report["core_electives"]["core_project"]["credits"]["remaining"] == 3
    assert report["department_electives"]["remaining"] == 15
    assert report["in_progress_credits"] == 24
    assert report["noncredit_requirements"]["conduct"]["remaining"] == 4
    assert report["noncredit_requirements"]["other_pe"]["remaining"] == 3
    assert len(report["requirement_rows"]) == 13
    assert {"MA2011", "MA3111", "MA3131", "MA6091"} <= context.blocked_codes
    assert "PH1034" not in context.blocked_codes
    assert "MA3103" in report["unavailable_needed_course_codes"]
    assert report["available_semester"] == "1151"
    assert (
        report["noncredit_requirements"]["learning_passport"]["required_hours"] is None
    )


@pytest.mark.parametrize(
    "code", ["MA2011", "MA2011A", "MA2011-A", "MA2011*", "ma2011-a"]
)
def test_main_course_code_matches_sections(code):
    assert course_code_key(code) == "MA2011"


@pytest.mark.parametrize(
    "field,value",
    [
        ("entry_year_roc", 114),
        ("department", "經濟學系"),
        ("program", "數學科學組"),
        ("other_programs_excluded", False),
    ],
)
def test_reject_wrong_scope(field, value):
    p = payload()
    p["student"][field] = value
    with pytest.raises(ValueError):
        build_context(p)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "needs_review"),
        ("status", []),
        ("credits", float("nan")),
        ("credits", -1),
        ("credits", True),
        ("source_rule_code", []),
        ("source_rule_code", "54101"),
        ("graduation_credit_eligible", "true"),
    ],
)
def test_invalid_records_rejected(field, value):
    p = payload()
    p["student"]["courses"][0][field] = value
    with pytest.raises(ValueError):
        build_context(p)


def test_retake_and_noncredit_counts():
    p = payload()
    row = copy.deepcopy(p["student"]["courses"][0])
    row["term"] = "1152"
    p["student"]["courses"].append(row)
    assert build_context(p).report["total_credits"]["completed"] == 77
    assert (
        build_context(p).report["noncredit_requirements"]["conduct"]["completed"] == 4
    )
    p["student"]["courses"].append(row)
    with pytest.raises(ValueError):
        build_context(p)


def test_special_project_and_no_automatic_elective_recognition():
    context = build_context(payload())
    assert context.contribution(course("p", "MA3133*"))["requirements"] == [
        "指定核心專題"
    ]
    assert context.contribution(course("g", "MA6099*"))["requirements"] == []
    p = payload()
    r = next(c for c in p["student"]["courses"] if c["course_id"] == "MA6091")
    r["status"] = "passed"
    report = build_context(p).report
    assert report["total_credits"]["completed"] == 77  # 採認仍為null
    assert report["passed_credits_pending_recognition"] == 3
    assert report["department_electives"]["completed"] == 6
    assert (
        report["core_electives"]["core_project"]["credits"]["remaining"] == 3
    )  # MA4071不替代


def test_mixed_science_never_completes_series():
    p = payload()
    p["student"]["courses"].append(
        {
            "term": "1152",
            "course_id": "CH1001",
            "name": "普通化學I",
            "credits": 3,
            "status": "passed",
            "source_rule_code": "13202",
            "graduation_credit_eligible": True,
        }
    )
    assert not build_context(p).report["natural_science"]["completed"]


def test_solver_prioritizes_gap_and_excludes_all_sections():
    context = build_context(payload())
    courses = [
        course("passed", "MA2011-A"),
        course("ongoing", "MA3111*"),
        course("project", "MA3133*", 2),
        course("project2", "MA3133A", 3),
        course("ge", "GS9000*", 4, ge_domain="人文"),
    ]
    prefs = Preferences(
        min_credits=3,
        max_credits=6,
        extra_course_codes=context.extra_candidate_codes(courses),
        excluded_course_codes=context.blocked_codes,
    )
    schedules, _ = solve(
        courses, [], prefs, score_fn=lambda c: context.contribution(c)["priority"]
    )
    assert schedules
    for s in schedules:
        keys = [course_code_key(c.code) for c in s.courses]
        assert len(keys) == len(set(keys))
        assert not set(keys) & {"MA2011", "MA3111"}
        assert not ts.any_conflict(s.courses)
    assert any(course_code_key(c.code) == "MA3133" for c in schedules[0].added)


def test_zero_credit_courses_cannot_fill_entire_week():
    courses = [
        course(str(i), f"PE20{i:02d}A", i, 0, ge_domain="體育") for i in range(1, 6)
    ]
    schedules, _ = solve(courses, [], Preferences(max_credits=3), score_fn=lambda _: 6)
    assert schedules
    assert all(sum(c.credits == 0 for c in s.added) <= 1 for s in schedules)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import app as web

    cs = [
        course("passed", "MA2011A"),
        course("ongoing", "MA3111*"),
        course("project", "MA3133A", 2),
        course("project2", "MA3133B", 3),
        course("ge", "GS9000*", 4, ge_domain="人文"),
    ]
    monkeypatch.setattr(web, "COURSES", cs)
    monkeypatch.setattr(web, "COURSE_BY_ID", {c.id: c for c in cs})
    monkeypatch.setattr(web, "DATA_META", {"semester": "1151"})
    monkeypatch.setattr(web, "ELIG_CACHE", {})
    monkeypatch.setattr(web, "DIST_CACHE", {})
    monkeypatch.setattr(web.app, "testing", True)
    return web.app.test_client()


def test_demo_and_graduation_api(api):
    assert api.get("/api/graduation/demo/not-a-program").status_code == 404
    d = api.get(f"/api/graduation/demo/{PROGRAM_ID}").get_json()["student"]
    result = api.post(
        "/api/graduation/check", json={"program_id": PROGRAM_ID, "student": d}
    )
    assert result.status_code == 200
    assert result.json["report"]["total_credits"]["completed"] == 77
    assert "畢業門檻與修課進度" in api.get("/").get_data(as_text=True)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {"program_id": "unknown", "student": {}},
        {"program_id": PROGRAM_ID, "student": None},
        {"program_id": PROGRAM_ID, "student": {"courses": []}},
    ],
)
def test_graduation_api_invalid_input_is_400(api, body):
    assert api.post("/api/graduation/check", json=body).status_code == 400


def test_schedules_api_uses_graduation_context(api):
    result = api.post(
        "/api/schedules",
        json={
            "fixed_ids": [],
            "user": {"dept": "數學系", "grade": 3},
            "graduation": payload(),
            "preferences": {"min_credits": 3, "max_credits": 6},
        },
    )
    assert result.status_code == 200
    assert result.json["schedules"]
    assert result.json["graduation_report"]["total_credits"]["completed"] == 77
    for schedule in result.json["schedules"]:
        assert not {c["id"] for c in schedule["courses"]} & {"passed", "ongoing"}
        assert all("graduation" in c for c in schedule["courses"])
    assert (
        api.post(
            "/api/schedules", json={"fixed_ids": ["passed"], "graduation": payload()}
        ).status_code
        == 400
    )
    assert (
        api.post(
            "/api/schedules",
            json={"fixed_ids": ["project", "project2"], "graduation": payload()},
        ).status_code
        == 400
    )
    assert api.post("/api/schedules", json={"fixed_ids": [{}]}).status_code == 400
    assert (
        api.post(
            "/api/schedules",
            json={"fixed_ids": [], "graduation": payload(), "user": {"dept": "經濟系"}},
        ).status_code
        == 400
    )


def test_free_slots_and_legacy_api_still_work(api):
    result = api.post(
        "/api/free_slots", json={"fixed_ids": [], "graduation": payload()}
    )
    assert result.status_code == 200
    assert "project" in {c["id"] for c in result.json["candidates"]}
    assert not {c["id"] for c in result.json["candidates"]} & {"passed", "ongoing"}
    legacy = api.post(
        "/api/schedules", json={"fixed_ids": [], "preferences": {"max_credits": 3}}
    )
    assert legacy.status_code == 200
    assert legacy.json["graduation_report"] is None


def test_group_budgets_limit_ge_and_physical_education():
    courses = [
        course("g1", "CC9001", 1, 2, ge_domain="人文"),
        course("g2", "CC9002", 2, 2, ge_domain="人文"),
        course("g3", "CC9003", 3, 2, ge_domain="人文"),
        course("pe1", "PE4051A", 4, 1),
        course("pe2", "PE4071A", 5, 1),
    ]
    prefs = Preferences(
        max_credits=9,
        extra_course_codes={"PE4051", "PE4071"},
        course_groups={"CC9001": "ge", "CC9002": "ge", "CC9003": "ge"},
        max_credits_per_group={"ge": 4},
        max_courses_per_prefix={"PE": 1},
    )
    schedules, _ = solve(courses, [], prefs, score_fn=lambda _: 6)
    assert schedules
    for s in schedules:
        assert sum(c.credits for c in s.added if c.code.startswith("CC")) <= 4
        assert sum(c.code.startswith("PE") for c in s.added) <= 1


def test_manual_completed_courses_excluded_without_graduation(api):
    result = api.post(
        "/api/schedules",
        json={
            "fixed_ids": [],
            "user": {"completed_courses": ["GS9000"]},
            "preferences": {"max_credits": 3},
        },
    )
    assert result.status_code == 200
    assert all(c["id"] != "ge" for s in result.json["schedules"] for c in s["courses"])


def test_invalid_user_dept_does_not_crash(api):
    result = api.post(
        "/api/schedules",
        json={"fixed_ids": [], "graduation": payload(), "user": {"dept": 123}},
    )
    assert result.status_code == 400


def test_fixed_conflicts_rejected_by_solver():
    courses = [course("p1", "MA3133A"), course("p2", "MA3103A")]
    with pytest.raises(ValueError, match="衝堂"):
        solve(courses, ["p1", "p2"])
