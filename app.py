"""
FastAPI 서버 — 범용 수업 조교 에이전트 빌더 (TA Builder)
PDF 업로드 → 에이전트 자동 생성 → 채팅
"""

import json
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chat import (
    clear_history,
    create_session,
    get_history_length,
    get_session,
    stream_chat,
    update_session_agents,
)
from generator import extract_text_from_pdf, generate_agents

load_dotenv()

app = FastAPI(title="TA Builder — 범용 수업 조교 에이전트 빌더")

static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    html_file = static_path / "index.html"
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))


# ─── PDF 업로드 → 에이전트 생성 ───────────────────────────────────────────────

@app.post("/generate")
async def generate(files: List[UploadFile] = File(...)):
    """PDF 파일 1~5개 업로드 → 에이전트 생성 → 세션 ID 반환"""
    if not files:
        raise HTTPException(status_code=400, detail="파일이 없습니다.")
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="파일은 최대 5개까지 업로드 가능합니다.")

    pdf_texts = []
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"{file.filename}: PDF 파일만 지원합니다.")
        content = await file.read()
        text = extract_text_from_pdf(content)
        if text.strip():
            pdf_texts.append(text)

    if not pdf_texts:
        raise HTTPException(status_code=400, detail="PDF에서 텍스트를 추출할 수 없습니다.")

    agents = await generate_agents(pdf_texts)
    session_id = create_session(agents)

    return {"session_id": session_id, "agents": agents}


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
