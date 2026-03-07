"""
PDF 분석 + 에이전트 자동 생성 모듈
PyMuPDF로 텍스트/이미지 추출 → Gemini LLM으로 2~4개 에이전트 정의 생성
"""

import asyncio
import json
import re
from io import BytesIO

import fitz  # PyMuPDF
from fastapi import HTTPException
from google.genai import types

from gemini_client import MODEL, get_client
from retriever import PageData

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


def extract_pdf_pages(file_bytes: bytes, filename: str = "") -> list[PageData]:
    """PDF 바이트에서 페이지별 PageData 리스트 추출"""
    label = filename or "첨부파일"
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        t = page.get_text() or ""
        if t.strip():
            source = f"{label}, p.{i + 1}"
            text = f"[{source}]\n{t}"
            pages.append(PageData(
                text=text,
                source=source,
                page_num=i,
                filename=label,
            ))
    doc.close()
    return pages


def extract_page_image(pdf_bytes: bytes, page_num: int, dpi: int = 150) -> bytes:
    """PDF의 특정 페이지(0-based)를 JPEG 이미지 바이트로 반환"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    img_bytes = pix.tobytes("jpeg")
    doc.close()
    return img_bytes


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
   d) 확인 후 답변 원칙 (가장 중요):
      - 첫 응답은 반드시 짧게(2~3문장) — 학생이 무엇을 원하는지 정확히 파악한 후 본 답변
      - 예: "어느 부분이 헷갈리셨나요?", "실습 중 막히신 건가요, 개념이 궁금하신 건가요?"
      - 학생의 의도가 명확해지면 그때 구체적이고 집중된 답변을 제공
      - 본 답변도 핵심만 간결하게, 불필요한 배경 설명은 생략
   e) 소크라테스식 대화법: 직접 답 대신 유도 질문으로 학생이 스스로 사고하도록 유도
   f) 한국어 학술 문체: 존댓말(~합니다/~하세요) 사용, 전문용어는 반드시 한국어(영어) 병기
      예: 회귀분석(regression), 분산(variance), 유의수준(significance level)
   g) 학생 수준 배려: 처음 배우는 학생 기준으로 비유와 구체적 예시 적극 활용
   h) 범위 외 질문 처리: 수업 내용과 무관한 질문은 정중히 안내 후 수업 관련 질문으로 유도
3. id는 "agent-1", "agent-2" 등 순서대로
4. 각 에이전트의 system_prompt는 서로 중복 없이 상호 보완적으로 설계할 것
"""


def _strip_code_fences(text: str) -> str:
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    return text.strip()


def _extract_first_json_array(text: str) -> str:
    """문자열에서 첫 JSON 배열 구간([ ... ])을 안전하게 추출."""
    start = text.find("[")
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for i, ch in enumerate(text[start:], start=start):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "\"":
                in_string = False
            continue

        if ch == "\"":
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]

    return ""


def _normalize_json_text(text: str) -> str:
    """LLM 출력에서 자주 나오는 JSON 잡음을 정리."""
    text = (
        text.replace("“", "\"")
        .replace("”", "\"")
        .replace("’", "'")
        .replace("‘", "'")
    )
    # trailing comma 제거: {"a":1,} / [1,2,]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    return text.strip()


def _parse_agents_json(raw_text: str) -> list[dict]:
    """LLM 원문에서 에이전트 JSON 배열을 최대한 복구해 파싱."""
    candidates: list[str] = []

    cleaned = _strip_code_fences(raw_text)
    if cleaned:
        candidates.append(cleaned)

    extracted = _extract_first_json_array(cleaned)
    if extracted:
        candidates.append(extracted)

    normalized_candidates: list[str] = []
    for c in candidates:
        normalized_candidates.append(c)
        nc = _normalize_json_text(c)
        if nc != c:
            normalized_candidates.append(nc)

    last_err = "empty candidates"
    for cand in normalized_candidates:
        try:
            parsed = json.loads(cand)
            if isinstance(parsed, dict) and isinstance(parsed.get("agents"), list):
                parsed = parsed["agents"]
            if isinstance(parsed, list):
                return parsed
            last_err = f"parsed type is {type(parsed).__name__}"
        except Exception as e:
            last_err = str(e)

    raise ValueError(last_err)


async def generate_agents(pdf_pages: list[PageData]) -> list[dict]:
    """
    PDF 페이지 목록을 분석하여 2~4개 에이전트 정의 반환
    반환: [{"id", "name", "role", "avatar", "color", "description", "system_prompt"}, ...]
    """
    # 에이전트 생성용 텍스트: 전체 합산 16000자 제한
    combined = "\n\n---\n\n".join(p.text for p in pdf_pages)[:16000]

    user_content = f"다음은 강의계획서/강의안입니다:\n\n{combined}\n\n이 수업에 맞는 2~4개의 조교 에이전트를 설계해주세요."

    client = get_client()
    last_err = ""
    response = None

    for attempt in range(3):
        try:
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT_FOR_GENERATOR,
                    max_output_tokens=4096,
                    temperature=0.3,
                    response_mime_type="application/json",
                ),
            )
            # 토큰 로깅
            if response.usage_metadata:
                u = response.usage_metadata
                print(
                    f"[generator] tokens — input: {u.prompt_token_count}, "
                    f"output: {u.candidates_token_count}, "
                    f"total: {u.total_token_count}"
                )
            break
        except Exception as e:
            last_err = str(e)
            if "429" in last_err or "quota" in last_err.lower():
                if attempt < 2:
                    await asyncio.sleep(60)
                    continue
            raise HTTPException(status_code=500, detail=f"에이전트 생성 중 오류: {last_err[:200]}")

    if response is None:
        raise HTTPException(
            status_code=429,
            detail="API 한도가 초과되었습니다. 잠시 후 다시 시도해주세요."
        )

    try:
        agents = _parse_agents_json(response.text or "")
    except Exception:
        # 1회 교정 시도: 깨진 JSON 텍스트를 모델로 다시 정규화
        try:
            repair_prompt = (
                "다음 텍스트를 JSON 배열로만 교정하세요. 설명 문장 없이 JSON만 출력하세요.\n\n"
                f"{response.text or ''}"
            )
            repaired = await client.aio.models.generate_content(
                model=MODEL,
                contents=repair_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=4096,
                    temperature=0.0,
                    response_mime_type="application/json",
                ),
            )
            agents = _parse_agents_json(repaired.text or "")
        except Exception:
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
