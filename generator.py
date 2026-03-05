"""
PDF 분석 + 에이전트 자동 생성 모듈
pypdf로 텍스트 추출 → Groq LLM으로 2~4개 에이전트 정의 생성
"""

import json
import re
from io import BytesIO

from fastapi import HTTPException
from pypdf import PdfReader

from key_manager import key_manager

MODEL = "qwen/qwen3-32b"

# 에이전트 아바타/색상 풀
AVATAR_POOL = ["📚", "🔬", "💡", "🗺️", "📊", "🏛️", "🧑‍💻", "🎯", "📝", "🔍"]
COLOR_POOL = [
    "#4a90d9",  # 파랑
    "#27ae60",  # 초록
    "#e67e22",  # 주황
    "#8e44ad",  # 보라
    "#e74c3c",  # 빨강
    "#16a085",  # 청록
    "#d4ac0d",  # 노랑
    "#2980b9",  # 하늘
]


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """PDF 바이트에서 텍스트 추출 (최대 8000자)"""
    reader = PdfReader(BytesIO(file_bytes))
    texts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        texts.append(t)
    full = "\n".join(texts)
    return full[:16000]


_SYSTEM_PROMPT_FOR_GENERATOR = """\
당신은 연세대학교 수업 조교 에이전트를 설계하는 전문가입니다.
강의계획서/강의안 텍스트를 분석하여 학생에게 가장 유용한 2~4개의 조교 에이전트를 설계합니다.

출력 형식: 반드시 아래 JSON 배열만 출력하세요. 다른 텍스트 없이.
[
  {
    "id": "agent-1",
    "name": "에이전트 이름 (한국어, 10자 이내)",
    "role": "역할 한 줄 설명 (20자 이내)",
    "description": "이 에이전트에게 물어볼 수 있는 것 (40자 이내)",
    "system_prompt": "에이전트 시스템 프롬프트 (아래 필수 요소 포함, 500~1000자)"
  }
]

에이전트 설계 원칙:
1. 각 에이전트는 명확히 구분되는 전문 영역을 담당한다
   예: 개념 설명 전문가, 과제/프로젝트 코치, 소프트웨어 실습 도우미, 문헌/자료 안내 등
2. system_prompt 필수 포함 요소 (모두 반영할 것):
   a) 페르소나: "당신은 이 수업의 전문 조교입니다. 강의 자료를 완벽히 숙지하고 있으며, 학생들이 스스로 학습할 수 있도록 돕습니다."
   b) 담당 영역 명시: 이 에이전트가 전담하는 주차별 내용, 핵심 개념·용어 목록을 강의 자료에서 직접 추출하여 열거
   c) 강의 자료 인용 의무: 답변 시 반드시 "강의 자료에 따르면...", "이 수업의 n주차 내용에서..." 형태로 인용
   d) 소크라테스식 대화법: 바로 답을 주는 대신 유도 질문으로 학생이 직접 사고하도록 유도
      예: "그 개념을 한 문장으로 설명해 보시겠어요?", "어디까지 이해하셨나요?"
   e) 한국어 학술 문체: 존댓말(~합니다/~하세요) 사용, 전문용어는 반드시 한국어(영어) 병기
      예: 회귀분석(regression), 분산(variance), 유의수준(significance level)
   f) 학생 수준 배려: 처음 배우는 학생 기준으로 비유와 구체적 예시 적극 활용
   g) 범위 외 질문 처리: 수업 내용과 무관한 질문은 정중히 안내 후 수업 관련 질문으로 유도
3. id는 "agent-1", "agent-2" 등 순서대로
4. 각 에이전트의 system_prompt는 서로 중복 없이 상호 보완적으로 설계할 것
"""


async def generate_agents(pdf_texts: list[str]) -> list[dict]:
    """
    PDF 텍스트 목록을 분석하여 2~4개 에이전트 정의 반환
    반환: [{"id", "name", "role", "avatar", "color", "description", "system_prompt"}, ...]
    """
    combined = "\n\n---\n\n".join(pdf_texts)
    # 전체 합산도 8000자로 제한
    combined = combined[:16000]

    user_content = f"다음은 강의계획서/강의안입니다:\n\n{combined}\n\n이 수업에 맞는 2~4개의 조교 에이전트를 설계해주세요."

    key_count = key_manager.count
    last_err = ""
    response = None

    for attempt in range(key_count):
        try:
            response = await key_manager.get_client().chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT_FOR_GENERATOR},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=4096,
                temperature=0.6,
                extra_body={"reasoning_format": "hidden"},
            )
            break  # 성공
        except Exception as e:
            last_err = str(e)
            if "429" in last_err or "rate_limit" in last_err.lower():
                if attempt < key_count - 1:
                    key_manager.rotate()  # 다음 키로 전환 후 즉시 재시도
                    continue
            else:
                raise HTTPException(status_code=500, detail=f"에이전트 생성 중 오류: {last_err[:200]}")

    if response is None:
        # 모든 키 소진
        wait_match = re.search(r"try again in (\d+)m(\d+\.?\d*)s", last_err)
        if wait_match:
            wait_msg = f"{wait_match.group(1)}분 {int(float(wait_match.group(2)))}초"
        else:
            wait_msg = "잠시"
        raise HTTPException(
            status_code=429,
            detail=f"모든 API 키의 한도가 초과되었습니다. {wait_msg} 후 다시 시도해주세요."
        )

    raw = response.choices[0].message.content or "[]"

    # JSON 파싱 전처리: think 블록 및 코드 블록 제거
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    raw = re.sub(r"```(?:json)?\s*", "", raw)
    raw = re.sub(r"```\s*$", "", raw)
    raw = raw.strip()

    try:
        agents = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="에이전트 JSON 파싱 실패. 다시 시도해주세요.")

    # 아바타/색상 할당 + CJK 문자 제거 (이름/역할에 중국어 등 혼입 방지)
    _cjk_pat = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u30ff]")
    for i, agent in enumerate(agents):
        agent["avatar"] = AVATAR_POOL[i % len(AVATAR_POOL)]
        agent["color"] = COLOR_POOL[i % len(COLOR_POOL)]
        agent["name"] = _cjk_pat.sub("", agent.get("name", "")).strip()
        agent["role"] = _cjk_pat.sub("", agent.get("role", "")).strip()
        agent["description"] = _cjk_pat.sub("", agent.get("description", "")).strip()
        agent["system_prompt"] = _cjk_pat.sub("", agent.get("system_prompt", "")).strip()

    return agents
