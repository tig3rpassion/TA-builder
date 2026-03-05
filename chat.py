"""
대화 관리 모듈 — 세션 기반
Groq API 스트리밍, 세션별 에이전트 + 대화 히스토리 관리
"""

import asyncio
import re
import uuid
from typing import AsyncGenerator, Optional

from key_manager import key_manager

KOREAN_ONLY_RULE = (
    "ABSOLUTE RULE — LANGUAGE: 모든 응답은 반드시 한국어로만 작성하세요. "
    "You MUST write EVERY sentence in Korean. "
    "Do NOT use English sentences, Spanish, French, German, Turkish, Arabic, Russian, or any other language. "
    "ONLY exception: technical/domain terms may appear as 한국어(English), e.g. 회귀분석(regression). "
    "If you notice yourself writing a non-Korean sentence, stop immediately and rewrite it in Korean. "
    "한국어로 답하지 않으면 실패입니다.\n\n"
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


MODEL = "qwen/qwen3-32b"
MAX_HISTORY = 20

# 세션 저장소: {session_id: {"agents": [...], "conversations": {agent_id: [...]}}}
sessions: dict[str, dict] = {}


def create_session(agents: list[dict], pdf_texts: list[str] = None) -> str:
    """새 세션 생성, session_id 반환"""
    session_id = str(uuid.uuid4())
    sessions[session_id] = {
        "agents": agents,
        "conversations": {a["id"]: [] for a in agents},
        "pdf_texts": pdf_texts or [],
    }
    return session_id


def get_session_pdf_texts(session_id: str) -> list[str]:
    session = sessions.get(session_id)
    if not session:
        return []
    return session.get("pdf_texts", [])


def append_pdf_texts(session_id: str, new_texts: list[str]) -> bool:
    session = sessions.get(session_id)
    if not session:
        return False
    session.setdefault("pdf_texts", []).extend(new_texts)
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
    return True


def _get_agent(session: dict, agent_id: str) -> Optional[dict]:
    for a in session["agents"]:
        if a["id"] == agent_id:
            return a
    return None


def _build_system_prompt(agent: dict, pdf_texts: list[str] = None) -> str:
    base = KOREAN_ONLY_RULE + agent["system_prompt"]
    if not pdf_texts:
        return base
    lecture_context = "\n---\n".join(pdf_texts)[:6000]
    return (
        base
        + "\n\n## 강의 자료 (반드시 참고하여 답변하세요)\n\n"
        + lecture_context
        + "\n\n## 답변 가이드라인\n"
        + "1. 위 강의 자료의 내용을 근거로 구체적으로 답하세요.\n"
        + "2. 가능하면 '강의 자료에 따르면...', '이 수업의 n주차 내용에서...' 형태로 인용하세요.\n"
        + "3. 자료에 없는 내용은 일반 지식으로 보충하되, 자료 기반 답변과 구분하여 명시하세요.\n"
        + "4. 학생이 스스로 생각할 수 있도록 유도 질문을 활용하세요.\n"
    )


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

    # API 호출 시 마지막 유저 메시지에 한국어 리마인더 삽입 (저장본은 원본 유지)
    api_history = history[:-1] + [{"role": "user", "content": f"[반드시 한국어로만 답하시오]\n{user_message}"}]
    pdf_texts = session.get("pdf_texts", [])
    messages = [{"role": "system", "content": _build_system_prompt(agent, pdf_texts)}] + api_history

    raw_chunks: list[str] = []
    key_count = key_manager.count
    MAX_ATTEMPTS = max(5, key_count * 2)  # 키 수만큼 추가 시도 허용

    for attempt in range(MAX_ATTEMPTS):
        try:
            raw_chunks = []
            stream = await key_manager.get_client().chat.completions.create(
                model=MODEL,
                messages=messages,
                max_tokens=2048,
                temperature=0.7,
                top_p=0.8,
                stream=True,
            )
            async for chunk in stream:
                text = _strip_cjk(chunk.choices[0].delta.content or "")
                raw_chunks.append(text)
                yield text
            break
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                # 다른 키가 있으면 즉시 전환
                rotated = key_manager.rotate()
                if rotated and attempt < MAX_ATTEMPTS - 1:
                    yield f"\n\n[API 키 전환 중... (키 {key_manager.current_index() + 1}/{key_count})]\n\n"
                    continue
                # 모든 키 소진 → 대기 후 재시도
                wait_match = re.search(r'try again in (\d+\.?\d*)s', err)
                wait_m = re.search(r'try again in (\d+)m(\d+\.?\d*)s', err)
                if wait_m:
                    wait = int(wait_m.group(1)) * 60 + float(wait_m.group(2)) + 2
                elif wait_match:
                    wait = float(wait_match.group(1)) + 2
                else:
                    wait = 62.0
                yield f"\n\n[모든 API 키 한도 초과 — {int(wait)}초 후 재시도합니다...]\n\n"
                await asyncio.sleep(wait)
            else:
                yield f"\n\n[오류: {err[:120]}]"
                break

    history.append({"role": "assistant", "content": _cleanup_artifacts("".join(raw_chunks))})


def clear_history(session_id: str, agent_id: str) -> bool:
    session = sessions.get(session_id)
    if not session or agent_id not in session["conversations"]:
        return False
    session["conversations"][agent_id] = []
    return True


def get_history_length(session_id: str, agent_id: str) -> int:
    session = sessions.get(session_id)
    if not session:
        return 0
    return len(session["conversations"].get(agent_id, []))
