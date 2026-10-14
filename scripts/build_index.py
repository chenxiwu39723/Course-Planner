# -*- coding: utf-8 -*-
"""階段 4：建立課程語意向量索引。

以「課名 + 大綱摘要（無大綱則課名 + 備註）」建索引，輸出：
- data/embeddings.npy        向量矩陣（已 L2 正規化）
- data/embeddings_meta.json  method / model / dim / ids 清單
- data/embeddings_tfidf.pkl  （僅 tfidf 後端）查詢用的向量器

後端由環境變數 EMBEDDING_PROVIDER 決定：
    local（預設）: sentence-transformers 本機模型，免 API 費用
    azure        : Azure OpenAI embeddings
若 local 但未安裝 sentence-transformers，自動退回 tfidf（本機、免費），流程不中斷。

可獨立執行：python scripts/build_index.py [--source live|sample]
隨機性固定（sklearn 無隨機；ST 以模型為準）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import pickle
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from planner.data_loader import load_courses  # noqa: E402
from planner.search import (  # noqa: E402
    course_text, encode_texts, EMB_PATH, META_PATH, TFIDF_PATH,
    DEFAULT_LOCAL_MODEL, _l2_normalize,
)

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def _resolve_method() -> tuple[str, str]:
    """決定後端與模型名；local 不可用時退回 tfidf。"""
    method = os.environ.get("EMBEDDING_PROVIDER", "local").lower()
    model = DEFAULT_LOCAL_MODEL
    if method == "local":
        try:
            import sentence_transformers  # noqa: F401
        except Exception:
            print("[build_index] 未安裝 sentence-transformers，改用 tfidf 本機退路。")
            print("            （要用 CLAUDE.md 指定的模型，請 pip install sentence-transformers）")
            method = "tfidf"
    return method, model


def _build_tfidf(texts: list[str]) -> np.ndarray:
    """用 sklearn TF-IDF 建向量（中文以字元 n-gram，免斷詞），並存向量器。"""
    from sklearn.feature_extraction.text import TfidfVectorizer
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), min_df=2)
    mat = vec.fit_transform(texts).astype(np.float32).toarray()
    TFIDF_PATH.write_bytes(pickle.dumps(vec))
    return _l2_normalize(mat)


def main() -> None:
    ap = argparse.ArgumentParser(description="階段 4：建立語意向量索引")
    ap.add_argument("--source", choices=["live", "sample"], default=None)
    args = ap.parse_args()

    courses, src = load_courses(args.source)
    ids = [c.id for c in courses]
    texts = [course_text(c) for c in courses]
    method, model = _resolve_method()
    print(f"資料來源：{src}；課程數：{len(courses)}；後端：{method}")

    if method == "tfidf":
        mat = _build_tfidf(texts)
    else:
        mat = encode_texts(texts, method, model)
    mat = np.asarray(mat, dtype=np.float32)

    EMB_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.save(EMB_PATH, mat)
    META_PATH.write_text(json.dumps({
        "method": method,
        "model": model if method != "tfidf" else "sklearn-tfidf-charwb-2-3",
        "dim": int(mat.shape[1]),
        "count": len(ids),
        "ids": ids,
    }, ensure_ascii=False), encoding="utf-8")

    print(f"完成：{mat.shape[0]} × {mat.shape[1]} → {EMB_PATH.relative_to(PROJECT_ROOT)}")
    if method == "tfidf":
        print(f"      向量器 → {TFIDF_PATH.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
