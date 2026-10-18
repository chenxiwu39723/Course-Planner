# -*- coding: utf-8 -*-
"""資料補完測試：大綱解析與課名語種推導（不涉網路）。"""
import importlib.util
import sys
from pathlib import Path

from planner.syllabus import parse_outline

ROOT = Path(__file__).resolve().parent.parent


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


enrich = _load_script("enrich_courses")


# ----------------------------------------------------------- 大綱解析
_OUTLINE_HTML = """
<html><body><table>
  <tr><td>開課單位</td><td>通識教育中心</td></tr>
  <tr><td>課號</td><td>GS2064-*</td></tr>
  <tr><td>課程領域</td><td>人文與思想領域</td></tr>
  <tr><td>授課內容</td><td>第一週 課程介紹\n第二週   音樂聆賞</td></tr>
</table></body></html>
"""


def test_parse_outline_extracts_fields():
    info = parse_outline(_OUTLINE_HTML)
    assert info["ge_domain"] == "人文與思想領域"
    assert info["open_unit"] == "通識教育中心"
    # 多餘空白被壓成單一空白
    assert info["syllabus_text"] == "第一週 課程介紹 第二週 音樂聆賞"


def test_parse_outline_missing_fields():
    info = parse_outline("<html><body><table><tr><td>課號</td><td>X</td></tr></table></body></html>")
    assert info["ge_domain"] is None
    assert info["syllabus_text"] is None


def test_parse_outline_label_must_match_exactly():
    # 「課程領域說明」不應被當成「課程領域」
    html = "<table><tr><td>課程領域說明</td><td>不應抓到</td></tr></table>"
    assert parse_outline(html)["ge_domain"] is None


# ----------------------------------------------------------- 課名語種推導
def test_derive_language():
    assert enrich.derive_language("日文(一)A") == "日語"
    assert enrich.derive_language("大一英文") == "英語"
    assert enrich.derive_language("西班牙文(一)") == "西班牙語"
    assert enrich.derive_language("韓語會話") == "韓語"
    assert enrich.derive_language("微積分") is None
    assert enrich.derive_language("") is None


def test_is_ge_or_lang():
    assert enrich._is_ge_or_lang({"code": "GS2064*", "dept": "通識教育中心"})
    assert enrich._is_ge_or_lang({"code": "LN0025A", "dept": "語言中心", "is_language": True})
    assert not enrich._is_ge_or_lang({"code": "CS1001", "dept": "資訊工程學系"})
