from __future__ import annotations

import asyncio
from typing import Any

from .router import IntentRouter
from .session_store import SessionKey, SessionStore
from .types import (
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    RouteResult,
)


class IntentDialogueAgent:
    """Stateful dialogue wrapper around the stateless intent router."""

    def __init__(
        self,
        router: IntentRouter,
        *,
        session_store: SessionStore | None = None,
        session_key: SessionKey | None = None,
    ) -> None:
        if (session_store is None) != (session_key is None):
            raise ValueError("session_store and session_key must be provided together")
        self.router = router
        self.session_store = session_store
        self.session_key = session_key
        self.history = DialogueHistory()
        self._lock = asyncio.Lock()

    @classmethod
    def from_config(
        cls,
        *args: Any,
        session_store: SessionStore | None = None,
        session_key: SessionKey | None = None,
        **kwargs: Any,
    ) -> "IntentDialogueAgent":
        return cls(
            IntentRouter.from_config(*args, **kwargs),
            session_store=session_store,
            session_key=session_key,
        )

    async def send(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> RouteResult:
        async with self._lock:
            await self._load_history()
            result = await self.router.route(
                query,
                dialogue_history=self.history,
                context=context,
                trace=trace,
            )
            self._append_turn(query, result)
            await self._save_history()
            return result

    async def send_no_loop(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> RouteResult:
        async with self._lock:
            await self._load_history()
            result = await self.router.route_no_loop(
                query,
                dialogue_history=self.history,
                context=context,
                trace=trace,
            )
            self._append_turn(query, result, route_mode="no_loop")
            await self._save_history()
            return result

    async def _load_history(self) -> None:
        if self.session_store is not None and self.session_key is not None:
            self.history = await self.session_store.load(self.session_key)

    async def _save_history(self) -> None:
        router_config = getattr(self.router, "config", None)
        session_config = getattr(router_config, "session", None)
        limit = getattr(session_config, "history_limit", 5)
        if limit == 0:
            self.history.turns.clear()
        elif len(self.history.turns) > limit:
            self.history.turns = self.history.turns[-limit:]
        if self.session_store is not None and self.session_key is not None:
            await self.session_store.save(self.session_key, self.history)

    def _append_turn(
        self,
        query: str,
        result: RouteResult,
        *,
        route_mode: str | None = None,
    ) -> None:
        metadata = {
            "loop_count": result.loop_count,
            "correction_scopes": list(result.correction_scopes),
            "visited_skills": list(result.visited_skills),
        }
        if route_mode:
            metadata["route_mode"] = route_mode
        self.history.turns.append(
            DialogueTurn(
                user_query=query,
                result=summarize_route_result(result),
                metadata=metadata,
            ),
        )

def summarize_route_result(result: RouteResult) -> DialogueRouteSummary:
    skill_id = result.skill.id if result.skill else None
    skill_name = result.skill.name if result.skill else None
    return DialogueRouteSummary(
        status=result.status,
        skill_id=skill_id,
        skill_name=skill_name,
        intent=result.intent,
        code=result.code,
        # Persist route semantics, not potentially sensitive extracted values.
        params={},
        confidence=result.confidence,
        question=result.question,
        options=result.options,
        reason=result.reason,
        resolved_query=result.resolved_query,
        context_relation=result.context_relation,
    )
