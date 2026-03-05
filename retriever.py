"""
TF-IDF 기반 RAG 검색 엔진
PDF 페이지별 데이터를 벡터화하여 관련 페이지를 코사인 유사도로 검색
"""

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


@dataclass
class PageData:
    text: str       # "[filename, p.N]\n본문"
    source: str     # "filename, p.N"
    page_num: int   # 0-based (PyMuPDF 이미지 추출용)
    filename: str   # 원본 파일명


@dataclass
class TfidfIndex:
    pages: list[PageData]
    vectorizer: TfidfVectorizer
    tfidf_matrix: object  # scipy sparse matrix


def build_index(pages: list[PageData]) -> TfidfIndex:
    """페이지 목록으로 TF-IDF 인덱스 구축"""
    texts = [p.text for p in pages]
    vectorizer = TfidfVectorizer(
        token_pattern=r"(?u)\b\w+\b",  # 한국어 단어 토큰화 지원
        sublinear_tf=True,
    )
    tfidf_matrix = vectorizer.fit_transform(texts)
    return TfidfIndex(pages=pages, vectorizer=vectorizer, tfidf_matrix=tfidf_matrix)


def search(index: TfidfIndex, query: str, top_k: int = 3) -> list[PageData]:
    """쿼리와 코사인 유사도가 높은 상위 top_k 페이지 반환 (유사도 0 제외)"""
    if not index.pages:
        return []
    query_vec = index.vectorizer.transform([query])
    sims = cosine_similarity(query_vec, index.tfidf_matrix).flatten()
    # 유사도 > 0인 것만 필터링 후 내림차순 정렬
    ranked = np.argsort(sims)[::-1]
    results = []
    for idx in ranked:
        if sims[idx] <= 0.0:
            break
        results.append(index.pages[idx])
        if len(results) >= top_k:
            break
    return results
