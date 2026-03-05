"""
Gemini 클라이언트 싱글턴 — key_manager.py 대체
GOOGLE_API_KEY 환경변수 단일 키 사용
"""

import os
from typing import Optional

from google import genai

MODEL = "gemini-2.5-flash"

_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    """lazy 싱글턴 Gemini 클라이언트 반환"""
    global _client
    if _client is None:
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY가 설정되지 않았습니다.")
        _client = genai.Client(api_key=api_key)
    return _client
