from __future__ import annotations

from typing import Any

from .router import IntentRouter
from .types import (
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    RouteResult,
)


class IntentDialogueAgent:
    """Stateful dialogue wrapper around the stateless intent router."""

    def __init__(self, router: IntentRouter) -> None:
        self.router = router
        self.history = DialogueHistory()

    @classmethod
    def from_config(
        cls,
        *args: Any,
        **kwargs: Any,
    ) -> "IntentDialogueAgent":
        return cls(IntentRouter.from_config(*args, **kwargs))

    async def send(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> RouteResult:
        result = await self.router.route(
            query,
            dialogue_history=self.history,
            context=context,
            trace=trace,
        )
        self.history.turns.append(
            DialogueTurn(
                user_query=query,
                result=summarize_route_result(result),
                metadata={
                    "loop_count": result.loop_count,
                    "correction_scopes": list(result.correction_scopes),
                    "visited_skills": list(result.visited_skills),
                },
            ),
        )
        return result

def summarize_route_result(result: RouteResult) -> DialogueRouteSummary:
    skill_id = result.skill.id if result.skill else None
    skill_name = result.skill.name if result.skill else None
    return DialogueRouteSummary(
        status=result.status,
        skill_id=skill_id,
        skill_name=skill_name,
        intent=result.intent,
        code=result.code,
        params=result.params,
        confidence=result.confidence,
        question=result.question,
        options=result.options,
        reason=result.reason,
    )
