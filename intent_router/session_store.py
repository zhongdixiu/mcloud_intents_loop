from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from .types import DialogueHistory


@dataclass(frozen=True)
class SessionKey:
    tenant_id: str
    user_id: str
    session_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.user_id or not self.session_id:
            raise ValueError("tenant_id, user_id, and session_id must all be non-empty")


class SessionStore(Protocol):
    async def load(self, key: SessionKey) -> DialogueHistory:
        ...

    async def save(self, key: SessionKey, history: DialogueHistory) -> None:
        ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._histories: dict[SessionKey, DialogueHistory] = {}
        self._lock = asyncio.Lock()

    async def load(self, key: SessionKey) -> DialogueHistory:
        async with self._lock:
            history = self._histories.get(key, DialogueHistory())
            return history.model_copy(deep=True)

    async def save(self, key: SessionKey, history: DialogueHistory) -> None:
        async with self._lock:
            self._histories[key] = history.model_copy(deep=True)

