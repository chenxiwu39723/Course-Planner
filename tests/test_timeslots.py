# -*- coding: utf-8 -*-
"""節次解析、衝堂判斷、空堂計算的測試。"""
from planner import timeslots as ts
from planner.models import Course


def _course(cid, times, credits=3.0):
    return Course(id=cid, code=cid, name=cid, teacher=[], dept="X",
                  credits=credits, times=times)


# ------------------------------------------------------- 節次代碼與時間表
def test_period_order_complete_and_z_between_4_and_5():
    assert len(ts.PERIOD_ORDER) == 16
    assert ts.PERIOD_ORDER[:6] == ["1", "2", "3", "4", "Z", "5"]
    # 每個節次都有對應時間
    for p in ts.PERIOD_ORDER:
        assert p in ts.PERIOD_TIMES


def test_period_times_are_sequential_hours():
    assert ts.PERIOD_TIMES["1"] == ("08:00", "08:50")
    assert ts.PERIOD_TIMES["Z"] == ("12:00", "12:50")
    assert ts.PERIOD_TIMES["5"] == ("13:00", "13:50")
    assert ts.PERIOD_TIMES["A"] == ("18:00", "18:50")


# ------------------------------------------------------- 機器碼解析
def test_parse_machine_basic():
    assert ts.parse_machine_classtime("27,28") == [(2, "7"), (2, "8")]


def test_parse_machine_noon_and_letters():
    assert ts.parse_machine_classtime("1Z") == [(1, "Z")]
    assert ts.parse_machine_classtime("3A,3B") == [(3, "A"), (3, "B")]


def test_parse_machine_skips_invalid():
    # 空 token、節次碼不存在(X)、星期非數字 皆略過
    assert ts.parse_machine_classtime("27,,9X,X5") == [(2, "7")]


def test_parse_machine_empty():
    assert ts.parse_machine_classtime("") == []


# ------------------------------------------------------- 可讀碼解析
def test_parse_readable_single_segment():
    assert ts.parse_readable_classtime("二78") == [(2, "7"), (2, "8")]
    assert ts.parse_readable_classtime("一234") == [(1, "2"), (1, "3"), (1, "4")]


def test_parse_readable_multi_segment():
    assert ts.parse_readable_classtime("一2 三5") == [(1, "2"), (3, "5")]


def test_parse_readable_with_letter_period():
    assert ts.parse_readable_classtime("五A") == [(5, "A")]


# ------------------------------------------------------- 衝堂
def test_has_conflict_true():
    a = _course("A", [(1, "2"), (1, "3")])
    b = _course("B", [(1, "3"), (1, "4")])
    assert ts.has_conflict(a, b) is True


def test_has_conflict_false():
    a = _course("A", [(1, "2"), (1, "3")])
    b = _course("B", [(2, "2"), (2, "3")])
    assert ts.has_conflict(a, b) is False


def test_any_conflict_pairs():
    a = _course("A", [(1, "2")])
    b = _course("B", [(1, "2")])
    c = _course("C", [(3, "5")])
    pairs = ts.any_conflict([a, b, c])
    assert pairs == [("A", "B")]


# ------------------------------------------------------- 空堂
def test_free_slots_excludes_occupied():
    a = _course("A", [(1, "2"), (1, "3")])
    free = ts.free_slots([a], days=[1], periods=["1", "2", "3", "4"])
    assert free == {(1, "1"), (1, "4")}


def test_free_slots_default_grid_size():
    free = ts.free_slots([])
    # 預設 週一到週五 × 16 節 = 80 格
    assert len(free) == 5 * len(ts.PERIOD_ORDER)


def test_fits_in_free_slots():
    free = {(1, "2"), (1, "3"), (2, "5")}
    assert ts.fits_in_free_slots(_course("A", [(1, "2"), (1, "3")]), free) is True
    assert ts.fits_in_free_slots(_course("B", [(1, "2"), (2, "6")]), free) is False
    # 無時間的課不算 fits
    assert ts.fits_in_free_slots(_course("C", []), free) is False
