"""
FastAPI 서버 — 범용 수업 조교 에이전트 빌더 (TA Builder)
PDF 업로드 → 에이전트 자동 생성 → 채팅
"""

import json
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chat import (
    append_pdf_data,
    clear_history,
    create_session,
    get_history_length,
    get_session,
    get_session_pdf_pages,
    stream_chat,
    update_session_agents,
)
from gemini_client import MissingApiKeyError, get_api_key_source, resolve_api_key
from generator import extract_pdf_pages, generate_agents

load_dotenv()

app = FastAPI(title="TA Builder — 범용 수업 조교 에이전트 빌더")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"서버 내부 오류: {str(exc)[:300]}"},
    )


@app.exception_handler(MissingApiKeyError)
async def missing_key_exception_handler(request: Request, exc: MissingApiKeyError):
    return JSONResponse(
        status_code=503,
        content={"detail": str(exc)},
    )


static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = static_path / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


@app.get("/health/config")
async def health_config():
    key_source = get_api_key_source()
    return {
        "status": "ok" if key_source else "degraded",
        "gemini_api_key_configured": bool(key_source),
        "api_key_source": key_source,
    }


def _ensure_ai_configured() -> None:
    """Gemini API 키 설정 여부를 사전 점검."""
    resolve_api_key()


# ─── PDF 업로드 → 에이전트 생성 ───────────────────────────────────────────────

@app.post("/generate")
async def generate(files: List[UploadFile] = File(...)):
    """PDF 파일 1~5개 업로드 → 에이전트 생성 → 세션 ID 반환"""
    _ensure_ai_configured()

    if not files:
        raise HTTPException(status_code=400, detail="파일이 없습니다.")
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="파일은 최대 5개까지 업로드 가능합니다.")

    all_pages = []
    pdf_bytes_map: dict[str, bytes] = {}

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{file.filename}: PDF 파일만 지원합니다.")
        content = await file.read()
        try:
            pages = extract_pdf_pages(content, file.filename)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{file.filename} 처리 중 오류: {str(e)[:200]}")
        if pages:
            all_pages.extend(pages)
            pdf_bytes_map[file.filename] = content

    if not all_pages:
        raise HTTPException(status_code=400, detail="PDF에서 텍스트를 추출할 수 없습니다.")

    agents = await generate_agents(all_pages)
    session_id = create_session(agents, pdf_pages=all_pages, pdf_bytes_map=pdf_bytes_map)

    return {"session_id": session_id, "agents": agents}


# ─── 에이전트 저장/불러오기 ──────────────────────────────────────────────────

@app.get("/agents/{session_id}/export")
async def export_agents(session_id: str):
    """에이전트 정의를 JSON으로 내보내기"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return {"agents": session["agents"]}


class ImportRequest(BaseModel):
    agents: list[dict]


@app.post("/agents/import")
async def import_agents(req: ImportRequest):
    """저장된 에이전트 JSON을 불러와 새 세션 생성"""
    if not req.agents:
        raise HTTPException(status_code=400, detail="에이전트 정보가 없습니다.")
    session_id = create_session(req.agents)
    return {"session_id": session_id, "agents": req.agents}


# ─── 자료 추가 ────────────────────────────────────────────────────────────────

@app.post("/add-material/{session_id}")
async def add_material(session_id: str, files: List[UploadFile] = File(...)):
    """기존 세션에 PDF 자료를 추가하고 에이전트를 재생성"""
    _ensure_ai_configured()

    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    new_pages = []
    new_bytes_map: dict[str, bytes] = {}

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{file.filename}: PDF 파일만 지원합니다.")
        content = await file.read()
        try:
            pages = extract_pdf_pages(content, file.filename)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"{file.filename} 처리 중 오류: {str(e)[:200]}")
        if pages:
            new_pages.extend(pages)
            new_bytes_map[file.filename] = content

    if not new_pages:
        raise HTTPException(status_code=400, detail="PDF에서 텍스트를 추출할 수 없습니다.")

    append_pdf_data(session_id, new_pages, new_bytes_map)
    all_pages = get_session_pdf_pages(session_id)

    new_agents = await generate_agents(all_pages)
    update_session_agents(session_id, new_agents)

    return {"session_id": session_id, "agents": new_agents}


# ─── 세션 에이전트 관리 ───────────────────────────────────────────────────────

@app.get("/agents/{session_id}")
async def get_agents(session_id: str):
    """세션의 에이전트 목록 반환"""
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return session["agents"]


class AgentsUpdateRequest(BaseModel):
    agents: list[dict]


@app.put("/agents/{session_id}")
async def update_agents(session_id: str, req: AgentsUpdateRequest):
    """에이전트 편집 (이름, 역할, system_prompt 등)"""
    ok = update_session_agents(session_id, req.agents)
    if not ok:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    return {"status": "ok", "agents": req.agents}


# ─── 채팅 ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    session_id: str
    agent_id: str
    message: str


@app.post("/chat")
async def chat(req: ChatRequest):
    """사용자 메시지를 받아 스트리밍으로 에이전트 응답 반환"""
    _ensure_ai_configured()

    session = get_session(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="메시지가 비어있습니다.")

    agent_ids = [a["id"] for a in session["agents"]]
    if req.agent_id not in agent_ids:
        raise HTTPException(status_code=404, detail="에이전트를 찾을 수 없습니다.")

    async def generate():
        try:
            async for chunk in stream_chat(req.session_id, req.agent_id, req.message):
                data = json.dumps({"type": "chunk", "text": chunk}, ensure_ascii=False)
                yield f"data: {data}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as e:
            error_data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat/clear/{session_id}/{agent_id}")
async def clear_chat(session_id: str, agent_id: str):
    """특정 에이전트의 대화 히스토리 초기화"""
    ok = clear_history(session_id, agent_id)
    if not ok:
        raise HTTPException(status_code=404, detail="세션 또는 에이전트를 찾을 수 없습니다.")
    return {"status": "ok", "session_id": session_id, "agent_id": agent_id}


@app.get("/chat/history/{session_id}/{agent_id}")
async def get_history_info(session_id: str, agent_id: str):
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "message_count": get_history_length(session_id, agent_id),
    }


if __name__ == "__main__":
    import uvicorn

    # lsof/kill은 로컬 전용 — Render 등 클라우드 환경에서는 건너뜀
    if not os.environ.get("RENDER"):
        import signal
        import subprocess
        import time

        try:
            result = subprocess.run(["lsof", "-ti", ":8000"], capture_output=True, text=True)
            pids = result.stdout.strip().splitlines()
            if pids:
                for pid in pids:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                time.sleep(1)
        except (FileNotFoundError, OSError):
            pass  # lsof 미설치 환경

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
