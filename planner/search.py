# -*- coding: utf-8 -*-
"""課程語意搜尋與推薦理由（階段 4，AI）。

- 以向量索引（data/embeddings.npy + meta）做語意搜尋，提供給 solver 的語意分數。
- 索引由 scripts/build_index.py 建立；後端可為：
    local  : sentence-transformers 本機模型（CLAUDE.md 預設，免 API 費用）
    tfidf  : sentence-transformers 未安裝時的本機退路（sklearn，免費）
    azure  : Azure OpenAI embeddings
- 推薦理由：由語言模型依「該課程的資料欄位」產生一句說明，不得編造內容；
  失敗/mock → 退回以欄位組成的確定性句子。
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Callable

import numpy as np

from .models import Course
from .llm_client import LLMClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
EMB_PATH = DATA_DIR / "embeddings.npy"
META_PATH = DATA_DIR / "embeddings_meta.json"
TFIDF_PATH = DATA_DIR / "embeddings_tfidf.pkl"

DEFAULT_LOCAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def course_text(course: Course) -> str:
    """建索引用的文字：課名(中英) + 大綱摘要；無大綱則用課名 + 備註。"""
    parts = [course.name]
    if course.name_en:
        parts.append(course.name_en)
    if course.syllabus_text and course.syllabus_text.strip():
        parts.append(course.syllabus_text[:500])
    elif course.notes:
        parts.append(course.notes)
    if course.ge_domain:
        parts.append(course.ge_domain)
    if course.dept:
        parts.append(course.dept)
    return " ".join(p for p in parts if p)


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


# ------------------------------------------------------------- 編碼後端
def encode_texts(texts: list[str], method: str, model_name: str) -> np.ndarray:
    """依後端把文字編成（已 L2 正規化）向量矩陣。供 build_index 與查詢共用。"""
    if method == "local":
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(model_name)
        vecs = model.encode(texts, normalize_embeddings=True,
                            show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)
    if method == "azure":
        import os
        from openai import AzureOpenAI
        client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
        )
        dep = os.environ["AZURE_OPENAI_EMBEDDING_DEPLOYMENT"]
        out = []
        for i in range(0, len(texts), 64):
            resp = client.embeddings.create(model=dep, input=texts[i:i + 64])
            out.extend([d.embedding for d in resp.data])
        return _l2_normalize(np.asarray(out, dtype=np.float32))
    raise ValueError(f"encode_texts 不支援的 method：{method}")


# ------------------------------------------------------------- 語意搜尋
class SemanticSearch:
    """載入索引並提供查詢。索引不存在時 available=False，語意分數一律 0。"""

    def __init__(self) -> None:
        self.available = False
        self.method = None
        self.model_name = DEFAULT_LOCAL_MODEL
        self.ids: list[str] = []
        self.matrix: np.ndarray | None = None
        self._tfidf = None
        self._st_model = None
        self._load()

    def _load(self) -> None:
        if not (EMB_PATH.exists() and META_PATH.exists()):
            return
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            self.matrix = np.load(EMB_PATH)
            self.ids = meta["ids"]
            self.method = meta["method"]
            self.model_name = meta.get("model", DEFAULT_LOCAL_MODEL)
            if self.method == "tfidf" and TFIDF_PATH.exists():
                self._tfidf = pickle.loads(TFIDF_PATH.read_bytes())
            if len(self.ids) == self.matrix.shape[0]:
                self.available = True
        except Exception as e:  # noqa: BLE001
            print(f"[search] 載入索引失敗（{e}），語意搜尋停用。")
            self.available = False

    def _encode_query(self, text: str) -> np.ndarray:
        if self.method == "tfidf":
            vec = self._tfidf.transform([text]).toarray().astype(np.float32)
            return _l2_normalize(vec)
        if self.method == "local":
            if self._st_model is None:
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer(self.model_name)
            vec = self._st_model.encode([text], normalize_embeddings=True,
                                        show_progress_bar=False)
            return np.asarray(vec, dtype=np.float32)
        if self.method == "azure":
            return encode_texts([text], "azure", self.model_name)
        raise ValueError(self.method)

    def query(self, text: str, top_k: int = 20) -> list[tuple[str, float]]:
        if not self.available or not text.strip():
            return []
        q = self._encode_query(text)[0]
        sims = self.matrix @ q                      # 皆已正規化 → 內積=cosine
        idx = np.argsort(-sims)[:top_k]
        return [(self.ids[i], float(sims[i])) for i in idx]

    def score_map(self, text: str) -> dict[str, float]:
        """回傳 {課號: 相似度}（0..1，負值截為 0）。索引不可用時回空 dict。"""
        if not self.available or not text.strip():
            return {}
        q = self._encode_query(text)[0]
        sims = self.matrix @ q
        return {cid: max(0.0, float(s)) for cid, s in zip(self.ids, sims)}

    def build_score_fn(self, topics: list[str], weight: float = 3.0
                       ) -> Callable[[Course], float]:
        """依 topics 組出 solver 用的語意分數函式（course → 分數）。"""
        query = " ".join(topics).strip()
        smap = self.score_map(query) if query else {}
        return lambda c: weight * smap.get(c.id, 0.0)


# ------------------------------------------------------------- 推薦理由
_REASON_SYSTEM = (
    "你是選課推薦助手。只能根據提供的課程欄位，用一句繁體中文（30 字內）說明"
    "為什麼這門課適合這位學生，連結到他的偏好。嚴禁編造課程內容或欄位沒有的資訊。"
    "只輸出那一句話，不要加引號或多餘文字。"
)


def _fallback_reason(course: Course, topics: list[str], relevant: bool) -> str:
    """不呼叫 AI 的確定性理由，只用欄位組成；僅在 relevant 時才提主題。"""
    bits = []
    if course.ge_domain:
        bits.append(f"屬「{course.ge_domain}」")
    if course.is_language and course.language:
        bits.append(f"{course.language}授課")
    if relevant and topics:
        bits.append(f"與你想學的「{topics[0]}」方向相關")
    tail = "；".join(bits) if bits else "可填入你的空堂、不衝堂"
    return f"「{course.name}」{tail}。"


def generate_reason(course: Course, topics: list[str], client: LLMClient,
                    *, relevant: bool = False) -> str:
    """為單門推薦課程產生一句理由。失敗/mock → 退回確定性句子。

    relevant：此課是否確實與使用者主題語意相關（避免對填補學分的無關課硬扯主題）。
    """
    fb = _fallback_reason(course, topics, relevant)
    if client.provider == "mock":
        return fb
    fields = {
        "課名": course.name, "英文名": course.name_en, "系所": course.dept,
        "教師": "、".join(course.teacher), "學分": course.credits,
        "通識領域": course.ge_domain, "授課語言": course.language,
        "備註": course.notes[:120],
    }
    topic_hint = topics if relevant else []
    messages = [
        {"role": "system", "content": _REASON_SYSTEM},
        {"role": "user", "content":
            f"學生想學的主題：{topic_hint}\n課程欄位：{json.dumps(fields, ensure_ascii=False)}"},
    ]
    try:
        text = client._call(messages, max_tokens=80).strip()  # noqa: SLF001
        text = text.strip().strip('「」"\' ')
        return text or fb
    except Exception:  # noqa: BLE001
        return fb


def generate_reasons(courses: list[Course], topics: list[str], client: LLMClient,
                     *, score_map: dict[str, float] | None = None,
                     threshold: float = 0.05) -> dict[str, str]:
    """為多門課產生理由。score_map 給定時，依相似度是否過門檻決定是否提主題。"""
    smap = score_map or {}
    return {
        c.id: generate_reason(c, topics, client,
                              relevant=smap.get(c.id, 0.0) >= threshold)
        for c in courses
    }
