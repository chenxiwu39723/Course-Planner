# -*- coding: utf-8 -*-
"""API 輸入驗證：不合法輸入一律回 400，不得 500 或拖垮伺服器。"""
import pytest

from planner.models import Course


def _course(cid, code, day):
    return Course(id=cid, code=code, name=code, teacher=[], dept="通識教育中心",
                  credits=2, times=[(day, "3")], ge_domain="人文")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    import app as web

    cs = [_course(f"c{i}", f"GS{i:04d}*", 1 + i % 5) for i in range(8)]
    monkeypatch.setattr(web, "COURSES", cs)
    monkeypatch.setattr(web, "COURSE_BY_ID", {c.id: c for c in cs})
    monkeypatch.setattr(web, "ELIG_CACHE", {})
    monkeypatch.setattr(web, "DIST_CACHE", {})
    monkeypatch.setattr(web.app, "testing", True)
    return web.app.test_client()


@pytest.mark.parametrize("value", [-1, 0, 6, 2.5, "3", True, None])
def test_schedules_rejects_bad_max_schedules(api, value):
    r = api.post("/api/schedules", json={"fixed_ids": [], "max_schedules": value})
    assert r.status_code == 400
    assert "max_schedules" in r.json["error"]


def test_schedules_default_and_valid_max_schedules(api):
    r = api.post("/api/schedules", json={"fixed_ids": []})
    assert r.status_code == 200 and len(r.json["schedules"]) <= 3
    r = api.post("/api/schedules", json={"fixed_ids": [], "max_schedules": 1})
    assert r.status_code == 200 and len(r.json["schedules"]) == 1


@pytest.mark.parametrize("prefs, keyword", [
    ({"min_credits": "abc"}, "min_credits"),
    ({"max_credits": -3}, "max_credits"),
    ({"max_credits": 1e9}, "max_credits"),
    ({"min_credits": 20, "max_credits": 10}, "不可大於"),
    ({"free_days": [9]}, "free_days"),
    ({"avoid_periods": [[1, "X"]]}, "avoid_periods"),
    ({"topics": {"a": 1}}, "topics"),
    ([1, 2], "preferences"),
])
def test_schedules_rejects_bad_preferences(api, prefs, keyword):
    for url in ("/api/schedules", "/api/free_slots"):
        r = api.post(url, json={"fixed_ids": [], "preferences": prefs})
        assert r.status_code == 400, url
        assert keyword in r.json["error"]


def test_parse_preferences_rejects_non_object(api):
    for body in ([1, 2], "text", 3):
        r = api.post("/api/parse_preferences", json=body)
        assert r.status_code == 400
    assert api.post("/api/parse_preferences", json={"text": "不要早八"}).status_code == 200


@pytest.mark.parametrize("limit", ["-5", "0", "201", "abc", "1.5"])
def test_courses_rejects_bad_limit(api, limit):
    r = api.get(f"/api/courses?limit={limit}")
    assert r.status_code == 400 and "limit" in r.json["error"]


def test_courses_limit_ok(api):
    r = api.get("/api/courses?limit=3")
    assert r.status_code == 200 and r.json["count"] == 3


def test_frontend_escapes_external_text():
    """課名、備註、AI 理由放進 innerHTML 前必須經過 esc()。"""
    import re
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "static" / "main.js").read_text(encoding="utf-8")
    assert "const esc =" in js
    # textContent / setAttribute 本身不解析 HTML，不需跳脫
    html_lines = [ln for ln in js.splitlines()
                  if "textContent" not in ln and "setAttribute" not in ln]
    for field in ("c.name", "c.reason", "c.eligibility.raw_notes", "a.name", "c.dept", "c.classroom"):
        raw = [ln for ln in html_lines if "${" + field + "}" in ln]
        assert not raw, f"{field} 未經 esc() 直接插入 HTML：{raw}"
