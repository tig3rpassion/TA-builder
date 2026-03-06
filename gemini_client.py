"""
Gemini 클라이언트 싱글턴
지원 키: GOOGLE_API_KEY, GEMINI_API_KEY
"""

import os
from typing import Optional, Tuple

from google import genai

MODEL = "gemini-2.5-flash"

_client: Optional[genai.Client] = None


class MissingApiKeyError(RuntimeError):
    """Gemini API 키가 설정되지 않았을 때 발생."""


def resolve_api_key() -> Tuple[str, str]:
    """환경변수에서 API 키를 찾고 (키값, 변수명) 반환."""
    candidates = ("GOOGLE_API_KEY", "GEMINI_API_KEY")
    for name in candidates:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    raise MissingApiKeyError(
        "Gemini API 키가 설정되지 않았습니다. "
        "Render Environment에 GOOGLE_API_KEY(권장) 또는 GEMINI_API_KEY를 추가하세요."
    )


def get_api_key_source() -> Optional[str]:
    """설정된 API 키 변수명을 반환. 없으면 None."""
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(name, "").strip():
            return name
    return None


def get_client() -> genai.Client:
    """lazy 싱글턴 Gemini 클라이언트 반환"""
    global _client
    if _client is None:
        api_key, _ = resolve_api_key()
        _client = genai.Client(api_key=api_key)
    return _client
