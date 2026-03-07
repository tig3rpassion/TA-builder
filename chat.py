"""
대화 관리 모듈 — 세션 기반
Gemini API 스트리밍, RAG 컨텍스트 + 멀티모달 이미지 주입, 세션별 에이전트 + 대화 히스토리 관리
"""

import asyncio
import json
import os
import re
import uuid
from typing import AsyncGenerator, Optional

from google.genai import types

from gemini_client import MODEL, get_client
from retriever import PageData, TfidfIndex, build_index, search

# 세션 파일 경로 (/tmp는 Render 슬립/웨이크 사이클에서도 유지됨)
_SESSIONS_FILE = os.path.join(os.environ.get("SESSIONS_DIR", "/tmp"), "ta_builder_sessions.json")


def _persist() -> None:
    """세션 전체를 파일에 저장 (직렬화 가능한 필드만)"""
    try:
        serializable = {}
        for sid, sess in sessions.items():
            serializable[sid] = {
                "agents": sess["agents"],
                "conversations": sess["conversations"],
                "pdf_pages": [
                    {
                        "text": p.text,
                        "source": p.source,
                        "page_num": p.page_num,
                        "filename": p.filename,
                    }
                    for p in sess.get("pdf_pages", [])
                ],
                # pdf_bytes_map, tfidf_index는 직렬화 제외
            }
        with open(_SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
    except Exception:
        pass


def _restore() -> None:
    """서버 시작 시 파일에서 세션 복원. tfidf_index는 pdf_pages에서 재구축."""
    try:
        if os.path.exists(_SESSIONS_FILE):
            with open(_SESSIONS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sid, sess in data.items():
                pdf_pages = [
                    PageData(
                        text=p["text"],
                        source=p["source"],
                        page_num=p["page_num"],
                        filename=p["filename"],
                    )
                    for p in sess.get("pdf_pages", [])
                ]
                tfidf_index = build_index(pdf_pages) if pdf_pages else None
                sessions[sid] = {
                    "agents": sess["agents"],
                    "conversations": sess["conversations"],
                    "pdf_pages": pdf_pages,
                    "pdf_bytes_map": {},       # 서버 재시작 후 소실 → 이미지 fallback
                    "tfidf_index": tfidf_index,
                }
    except Exception:
        pass


KOREAN_ONLY_RULE = (
    "ABSOLUTE RULE — LANGUAGE: 모든 응답은 반드시 한국어로만 작성하세요. "
    "You MUST write EVERY sentence in Korean. "
    "Do NOT use English sentences, Spanish, French, German, Turkish, Arabic, Russian, or any other language. "
    "ONLY exception: technical/domain terms may appear as 한국어(English), e.g. 회귀분석(regression). "
    "If you notice yourself writing a non-Korean sentence, stop immediately and rewrite it in Korean. "
    "한국어로 답하지 않으면 실패입니다.\n\n"
)

BREVITY_RULE = (
    "ABSOLUTE RULE — RESPONSE STYLE:\n"
    "【1단계 — 확인】학생의 질문 의도가 불분명하거나 범위가 넓으면, "
    "먼저 2~3문장으로 짧게 확인 질문만 하세요. 이 단계에서 본 답변을 하지 마세요.\n"
    "【2단계 — 본 답변】의도가 파악되면 핵심만 간결하게 답하세요. "
    "서론, 배경 설명, 요약 반복은 절대 포함하지 마세요. "
    "본 답변은 반드시 10문장 이내로 작성하세요.\n\n"
)

TA_PERSONA = (
    "당신은 연세대학교 대학원 수업 조교입니다. "
    "강의 자료를 완벽히 숙지하고 있으며, 학생들이 스스로 학습할 수 있도록 돕습니다.\n\n"
)

VISUAL_INSTRUCTION = (
    "\n\n## 시각 자료 참조 지침\n"
    "- 함께 제공되는 슬라이드 이미지를 반드시 참고하여 답변하세요.\n"
    "- 도표·그래프·수식이 이미지에 포함된 경우, 텍스트와 함께 설명하세요.\n"
    "- 이미지 내 내용을 인용할 때는 '(슬라이드 이미지 참조)' 로 명시하세요.\n"
)

_cjk_re = re.compile(
    r'[\u4e00-\u9fff'   # 한자 (CJK Unified Ideographs)
    r'\u3400-\u4dbf'    # 한자 확장 A
    r'\u3040-\u309f'    # 히라가나
    r'\u30a0-\u30ff'    # 카타카나
    r']'
)


def _strip_cjk(text: str) -> str:
    """한자·일본어 가나만 제거. 한국어·영어·기호는 유지."""
    return _cjk_re.sub('', text)


def _cleanup_artifacts(text: str) -> str:
    """필터링 후 남은 잔재 정리 (문장 앞 쉼표, 이중 공백 등)"""
    text = re.sub(r'(?m)^,\s*', '', text)
    text = re.sub(r',\s*,', ',', text)
    text = re.sub(r'  +', ' ', text)
    text = re.sub(r'\s+(를|을|이|가|은|는|의|에|로|으로|와|과)\b', r'\1', text)
    return text


def _build_citation_footer(rag_pages: list[PageData]) -> str:
    """응답 말미에 붙일 근거 자료 목록 생성."""
    if not rag_pages:
        return "\n\n근거 자료\n- 관련 페이지를 찾지 못했습니다."

    seen = set()
    ordered_sources: list[str] = []
    for page in rag_pages:
        src = page.source.strip()
        if src and src not in seen:
            seen.add(src)
            ordered_sources.append(src)

    lines = ["근거 자료"] + [f"- {src}" for src in ordered_sources]
    return "\n\n" + "\n".join(lines)


def _select_diverse_pages(pages: list[PageData], max_pages: int = 5, max_per_file: int = 2) -> list[PageData]:
    """한 파일에 편중되지 않도록 페이지를 고르게 선택."""
    selected: list[PageData] = []
    file_counts: dict[str, int] = {}

    for page in pages:
        fname = page.filename or ""
        if file_counts.get(fname, 0) >= max_per_file:
            continue
        selected.append(page)
        file_counts[fname] = file_counts.get(fname, 0) + 1
        if len(selected) >= max_pages:
            break
    return selected


def _fallback_pages_from_session(session: dict, max_pages: int = 5) -> list[PageData]:
    """RAG 검색 실패 시 파일별 대표 페이지를 fallback으로 선택."""
    all_pages: list[PageData] = session.get("pdf_pages") or []
    if not all_pages:
        return []

    first_by_file: list[PageData] = []
    seen_files = set()
    for page in all_pages:
        fname = page.filename or ""
        if fname in seen_files:
            continue
        seen_files.add(fname)
        first_by_file.append(page)
        if len(first_by_file) >= max_pages:
            break

    if first_by_file:
        return first_by_file
    return all_pages[:max_pages]


def _supplement_with_other_files(selected: list[PageData], session: dict, max_pages: int = 5) -> list[PageData]:
    """선택된 페이지가 한 파일에 편중되면 다른 파일 대표 페이지를 보충."""
    if not selected:
        return selected

    all_pages: list[PageData] = session.get("pdf_pages") or []
    if not all_pages:
        return selected

    selected_files = {p.filename for p in selected}
    file_count_total = len({p.filename for p in all_pages if p.filename})
    if file_count_total <= 1 or len(selected_files) >= 2:
        return selected

    # 아직 포함되지 않은 파일의 첫 페이지를 순서대로 보충
    for page in all_pages:
        if len(selected) >= max_pages:
            break
        if not page.filename or page.filename in selected_files:
            continue
        selected.append(page)
        selected_files.add(page.filename)

    return selected


MAX_HISTORY = 20

# 세션 저장소
sessions: dict[str, dict] = {}
_restore()


def create_session(
    agents: list[dict],
    pdf_pages: list[PageData] = None,
    pdf_bytes_map: dict[str, bytes] = None,
) -> str:
    """새 세션 생성, session_id 반환"""
    session_id = str(uuid.uuid4())
    pages = pdf_pages or []
    tfidf_index = build_index(pages) if pages else None
    sessions[session_id] = {
        "agents": agents,
        "conversations": {a["id"]: [] for a in agents},
        "pdf_pages": pages,
        "pdf_bytes_map": pdf_bytes_map or {},
        "tfidf_index": tfidf_index,
    }
    _persist()
    return session_id


def get_session_pdf_pages(session_id: str) -> list[PageData]:
    session = sessions.get(session_id)
    if not session:
        return []
    return session.get("pdf_pages", [])


def append_pdf_data(
    session_id: str,
    new_pages: list[PageData],
    new_bytes_map: dict[str, bytes] = None,
) -> bool:
    """새 페이지 추가 후 TF-IDF 인덱스 재구축"""
    session = sessions.get(session_id)
    if not session:
        return False
    session.setdefault("pdf_pages", []).extend(new_pages)
    if new_bytes_map:
        session.setdefault("pdf_bytes_map", {}).update(new_bytes_map)
    session["tfidf_index"] = build_index(session["pdf_pages"])
    _persist()
    return True


def get_session(session_id: str) -> Optional[dict]:
    return sessions.get(session_id)


def update_session_agents(session_id: str, agents: list[dict]) -> bool:
    """에이전트 정의 업데이트 (이름, 역할, system_prompt 등). 대화 히스토리 유지."""
    session = sessions.get(session_id)
    if not session:
        return False
    old_convos = session["conversations"]
    new_convos = {}
    for agent in agents:
        aid = agent["id"]
        new_convos[aid] = old_convos.get(aid, [])
    session["agents"] = agents
    session["conversations"] = new_convos
    _persist()
    return True


def _get_agent(session: dict, agent_id: str) -> Optional[dict]:
    for a in session["agents"]:
        if a["id"] == agent_id:
            return a
    return None


def _build_system_prompt(agent: dict) -> str:
    """시스템 프롬프트 구성 (강의 자료는 contents에 RAG로 주입)"""
    return (
        KOREAN_ONLY_RULE
        + BREVITY_RULE
        + TA_PERSONA
        + agent["system_prompt"]
        + VISUAL_INSTRUCTION
    )


def _build_gemini_history(history: list[dict]) -> list[types.Content]:
    """대화 히스토리를 Gemini Content 형식으로 변환"""
    contents = []
    for msg in history:
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(types.Content(
            role=role,
            parts=[types.Part.from_text(text=msg["content"])],
        ))
    return contents


async def stream_chat(
    session_id: str, agent_id: str, user_message: str
) -> AsyncGenerator[str, None]:
    """사용자 메시지를 받아 에이전트 응답을 스트리밍으로 반환"""
    session = sessions.get(session_id)
    if not session:
        yield "[오류: 세션을 찾을 수 없습니다]"
        return

    agent = _get_agent(session, agent_id)
    if not agent:
        yield "[오류: 에이전트를 찾을 수 없습니다]"
        return

    history = session["conversations"][agent_id]
    history.append({"role": "user", "content": user_message})

    if len(history) > MAX_HISTORY:
        session["conversations"][agent_id] = history[-MAX_HISTORY:]
        history = session["conversations"][agent_id]

    # RAG: 관련 페이지 검색
    tfidf_index: Optional[TfidfIndex] = session.get("tfidf_index")
    pdf_bytes_map: dict[str, bytes] = session.get("pdf_bytes_map", {})
    rag_candidates = search(tfidf_index, user_message, top_k=8) if tfidf_index else []
    rag_pages = _select_diverse_pages(rag_candidates, max_pages=5, max_per_file=2)
    if not rag_pages:
        # 유사도 0이어도 최소한 파일별 대표 페이지를 근거로 제시
        rag_pages = _fallback_pages_from_session(session, max_pages=5)
    else:
        # 검색 결과가 한 파일에만 몰리면 다른 파일 페이지를 보충
        rag_pages = _supplement_with_other_files(rag_pages, session, max_pages=5)

    # Gemini contents 구성
    # 이전 대화 히스토리 (마지막 user 제외)
    gemini_history = _build_gemini_history(history[:-1])

    # 현재 user turn parts 구성
    user_parts: list[types.Part] = []

    if rag_pages:
        user_parts.append(types.Part.from_text(text="관련 강의 자료:"))
        for page in rag_pages:
            user_parts.append(types.Part.from_text(text=page.text))
            # 이미지 주입 (pdf_bytes_map에 해당 파일 있을 때)
            if page.filename in pdf_bytes_map:
                try:
                    from generator import extract_page_image
                    img_bytes = extract_page_image(
                        pdf_bytes_map[page.filename], page.page_num, dpi=150
                    )
                    user_parts.append(types.Part.from_bytes(
                        data=img_bytes,
                        mime_type="image/jpeg",
                    ))
                except Exception:
                    pass  # 이미지 추출 실패 시 텍스트만 사용
        user_parts.append(types.Part.from_text(text="---"))

    # 한국어 리마인더 포함 실제 질문
    user_parts.append(types.Part.from_text(
        text=f"[반드시 한국어로만 답하시오]\n{user_message}"
    ))

    current_user_content = types.Content(role="user", parts=user_parts)
    all_contents = gemini_history + [current_user_content]

    client = get_client()
    system_prompt = _build_system_prompt(agent)

    raw_chunks: list[str] = []

    for attempt in range(3):
        try:
            raw_chunks = []
            stream = await client.aio.models.generate_content_stream(
                model=MODEL,
                contents=all_contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=2048,
                    temperature=0.7,
                    top_p=0.8,
                ),
            )
            async for chunk in stream:
                text = chunk.text or ""
                text = _strip_cjk(text)
                raw_chunks.append(text)
                if text:
                    yield text

            # 토큰 로깅 (스트림 종료 후 usage_metadata 접근)
            try:
                if hasattr(stream, "usage_metadata") and stream.usage_metadata:
                    u = stream.usage_metadata
                    print(
                        f"[chat] tokens — input: {u.prompt_token_count}, "
                        f"output: {u.candidates_token_count}, "
                        f"total: {u.total_token_count}"
                    )
            except Exception:
                pass
            break

        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                if attempt < 2:
                    yield f"\n\n[API 한도 초과 — 60초 후 재시도합니다...]\n\n"
                    await asyncio.sleep(60)
                    continue
                else:
                    yield f"\n\n[오류: API 한도 초과. 잠시 후 다시 시도해주세요.]"
            else:
                yield f"\n\n[오류: {err[:120]}]"
            break

    answer_text = _cleanup_artifacts("".join(raw_chunks))
    citation_footer = _build_citation_footer(rag_pages)
    if citation_footer:
        yield citation_footer
        answer_text += citation_footer

    history.append({"role": "assistant", "content": answer_text})
    _persist()


def clear_history(session_id: str, agent_id: str) -> bool:
    session = sessions.get(session_id)
    if not session or agent_id not in session["conversations"]:
        return False
    session["conversations"][agent_id] = []
    _persist()
    return True


def get_history_length(session_id: str, agent_id: str) -> int:
    session = sessions.get(session_id)
    if not session:
        return 0
    return len(session["conversations"].get(agent_id, []))
