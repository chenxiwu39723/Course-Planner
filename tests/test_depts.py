# -*- coding: utf-8 -*-
"""學生系所清單：清理開課單位字串。"""
from planner.depts import classify, split_units, student_depts
from planner.models import Course


def _c(dept: str) -> Course:
    return Course(id=dept, code="X", name="測試", teacher=[], dept=dept, credits=3.0, times=[])


def test_split_units_removes_revoked_and_duplicates():
    assert split_units("(已撤銷)法律與政府學系、法律與政府學系") == ["法律與政府學系"]
    assert split_units("營建管理研究所碩士班、土木工程學系營建管理碩士班") == [
        "營建管理研究所碩士班", "土木工程學系營建管理碩士班"]


def test_classify():
    assert classify("資訊工程學系") == "undergrad"
    assert classify("生命科學系") == "undergrad"
    assert classify("資訊電機學院學士班") == "undergrad"
    assert classify("統計研究所碩士班") == "graduate"
    assert classify("人工智慧國際碩士學位學程") == "graduate"
    assert classify("環境科技博士學位學程(台灣聯合大學系統)") == "graduate"
    for unit in ("體育室", "語言中心", "通識教育中心", "核心通識課程", "工學院",
                 "學務處-服務學習發展中心", "臺灣大專院校人工智慧學程聯盟", "（未分類）"):
        assert classify(unit) is None, unit


def test_student_depts_groups():
    courses = [_c("體育室"), _c("數學系"), _c("數學系"), _c("工學院"),
               _c("(已撤銷)法律與政府學系、法律與政府學系"), _c("統計研究所碩士班")]
    assert student_depts(courses) == {
        "undergrad": ["數學系", "法律與政府學系"],
        "graduate": ["統計研究所碩士班"],
    }
