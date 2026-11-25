# -*- coding: utf-8 -*-
"""語意搜尋與推薦理由測試（以記憶體內建的小索引，不依賴已產生的檔案）。"""
import numpy as np
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer

from planner.models import Course
from planner.llm_client import LLMClient
from planner.search import (
    SemanticSearch, course_text, _l2_normalize, generate_reason, generate_reasons,
)


def _course(cid, name, notes="", syllabus=None, ge=None, lang=None):
    return Course(id=cid, code=cid, name=name, teacher=[], dept="X", credits=3.0,
                  times=[(1, "2")], notes=notes, syllabus_text=syllabus,
                  ge_domain=ge, language=lang, is_language=bool(lang))


def _memory_search(docs, ids):
    """用 tfidf 在記憶體建一個小索引，略過檔案載入。"""
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3))
    mat = _l2_normalize(vec.fit_transform(docs).astype(np.float32).toarray())
    ss = SemanticSearch.__new__(SemanticSearch)
    ss.available = True
    ss.method = "tfidf"
    ss.model_name = "test"
    ss._tfidf = vec
    ss.matrix = mat
    ss.ids = ids
    ss._st_model = None
    return ss


# ------------------------------------------------------ course_text
def test_course_text_prefers_syllabus():
    c = _course("A", "資料科學", notes="備註", syllabus="這門課教統計與資料分析")
    txt = course_text(c)
    assert "資料分析" in txt and "資料科學" in txt


def test_course_text_falls_back_to_notes():
    c = _course("A", "資料科學", notes="需先修統計")
    assert "需先修統計" in course_text(c)


# ------------------------------------------------------ 查詢
def test_query_ranks_relevant_first():
    ss = _memory_search(["資料分析與統計方法", "日語會話入門", "音樂鑑賞"],
                        ["DATA", "JP", "MUS"])
    res = ss.query("資料分析", top_k=3)
    assert res[0][0] == "DATA"


def test_score_map_unrelated_is_low():
    ss = _memory_search(["資料分析與統計", "音樂鑑賞"], ["DATA", "MUS"])
    smap = ss.score_map("資料分析")
    assert smap["DATA"] > smap["MUS"]


def test_build_score_fn_uses_similarity():
    ss = _memory_search(["資料分析與統計", "音樂鑑賞"], ["DATA", "MUS"])
    fn = ss.build_score_fn(["資料分析"])
    data_c = _course("DATA", "資料分析")
    mus_c = _course("MUS", "音樂鑑賞")
    assert fn(data_c) > fn(mus_c)


def test_unavailable_search_scores_zero():
    ss = SemanticSearch.__new__(SemanticSearch)
    ss.available = False
    ss.method = None
    ss.ids = []
    ss.matrix = None
    ss._tfidf = None
    ss._st_model = None
    assert ss.query("任何", 5) == []
    assert ss.score_map("任何") == {}
    assert ss.build_score_fn(["資料分析"])(_course("X", "X")) == 0.0


# ------------------------------------------------------ 推薦理由
def test_reason_mock_relevant_mentions_topic():
    client = LLMClient(provider="mock")
    c = _course("A", "資料科學導論", ge="自然與科學領域")
    r = generate_reason(c, ["資料分析"], client, relevant=True)
    assert "資料分析" in r and "資料科學導論" in r


def test_reason_mock_irrelevant_omits_topic():
    client = LLMClient(provider="mock")
    c = _course("B", "哲學思辨", ge="人文與思想領域")
    r = generate_reason(c, ["資料分析"], client, relevant=False)
    assert "資料分析" not in r          # 不得對無關課硬扯主題
    assert "哲學思辨" in r


class _FakeLLM(LLMClient):
    """模擬真實供應商：記錄每次送出的訊息，回傳指定文字。"""

    def __init__(self, reply):
        super().__init__(provider="azure_openai")
        self.reply, self.calls = reply, []

    def _call(self, messages, max_tokens):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_reasons_batched_in_one_json_call():
    import json
    cs = [_course("A", "資料科學", ge="自然"), _course("B", "哲學", ge="人文"),
          _course("A", "資料科學", ge="自然")]                 # 重複課只問一次
    llm = _FakeLLM(json.dumps({"reasons": {"A": "可接觸資料分析方法", "B": "拓展人文視野"}}))
    reasons = generate_reasons(cs, ["資料分析"], llm, score_map={"A": 0.5})
    assert reasons == {"A": "可接觸資料分析方法", "B": "拓展人文視野"}
    assert len(llm.calls) == 1
    # Azure JSON 模式要求訊息中出現 "json" 字樣，否則整個請求被拒
    assert "json" in " ".join(m["content"] for m in llm.calls[0]).lower()
    assert '"id": "A"' in llm.calls[0][1]["content"]


def test_reasons_invalid_entries_fall_back_per_course():
    import json
    cs = [_course("A", "資料科學", ge="自然"), _course("B", "哲學", ge="人文"),
          _course("C", "體育", ge=None)]
    llm = _FakeLLM(json.dumps({"reasons": {
        "A": "很好" * 40,                       # 過長
        "B": "能培養資料分析能力",                 # 無關課硬扯主題
        "C": "活動身體、調劑課業",
    }}))
    reasons = generate_reasons(cs, ["資料分析"], llm, score_map={})
    assert reasons["A"].startswith("「資料科學」")      # 退回確定性句子
    assert "資料分析" not in reasons["B"]
    assert reasons["C"] == "活動身體、調劑課業"


@pytest.mark.parametrize("reply", ["不是 JSON", '{"reasons": "x"}', TimeoutError("逾時")])
def test_reasons_llm_failure_falls_back(reply):
    llm = _FakeLLM(reply)
    reasons = generate_reasons([_course("A", "資料科學", ge="自然")], ["資料分析"], llm,
                               score_map={"A": 0.5})
    assert reasons["A"] == "「資料科學」屬「自然」；與你想學的「資料分析」方向相關。"


def test_generate_reasons_gated_by_score_map():
    client = LLMClient(provider="mock")
    cs = [_course("A", "資料科學", ge="自然"), _course("B", "哲學", ge="人文")]
    reasons = generate_reasons(cs, ["資料分析"], client,
                               score_map={"A": 0.5, "B": 0.0}, threshold=0.05)
    assert "資料分析" in reasons["A"]
    assert "資料分析" not in reasons["B"]
