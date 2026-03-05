"""
Groq API 키 로테이션 매니저
.env에서 GROQ_API_KEY_1, GROQ_API_KEY_2 ... 순서로 로드
429 발생 시 다음 키로 자동 전환
"""

import os
from typing import Optional

from groq import AsyncGroq


class KeyManager:
    def __init__(self):
        self._keys: list[str] = []
        self._clients: list[Optional[AsyncGroq]] = []
        self._current: int = 0
        self._loaded: bool = False

    def _load(self) -> None:
        if self._loaded:
            return
        keys = []
        for i in range(1, 20):
            k = os.environ.get(f"GROQ_API_KEY_{i}", "").strip()
            if k:
                keys.append(k)
        # 번호 없는 단일 키도 지원 (하위 호환)
        if not keys:
            k = os.environ.get("GROQ_API_KEY", "").strip()
            if k:
                keys.append(k)
        self._keys = keys
        self._clients = [None] * len(keys)
        self._loaded = True

    @property
    def count(self) -> int:
        self._load()
        return len(self._keys)

    def get_client(self) -> AsyncGroq:
        self._load()
        if not self._keys:
            raise RuntimeError("GROQ_API_KEY가 설정되지 않았습니다.")
        idx = self._current % len(self._keys)
        if self._clients[idx] is None:
            self._clients[idx] = AsyncGroq(api_key=self._keys[idx])
        return self._clients[idx]

    def rotate(self) -> bool:
        """다음 키로 전환. 한 바퀴 돌아 시작 키로 돌아오면 False 반환."""
        self._load()
        if len(self._keys) <= 1:
            return False
        start = self._current
        self._current = (self._current + 1) % len(self._keys)
        return self._current != start

    def current_index(self) -> int:
        return self._current % max(len(self._keys), 1)


# 싱글턴
key_manager = KeyManager()
