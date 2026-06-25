from __future__ import annotations

import builtins

from intent_router import cli
from intent_router.types import RouteResult, SkillRef


async def test_chat_ignores_blank_input(monkeypatch, capsys) -> None:
    class FakeAgent:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def send(self, query: str, *, trace=None) -> RouteResult:
            self.queries.append(query)
            return RouteResult(
                status="matched",
                skill=SkillRef(id="mcloud_search_skill", name="云盘搜索"),
                intent="搜综合",
                code="018",
                loop_count=1,
                resolved_query=query,
                context_relation="new_request",
            )

    fake_agent = FakeAgent()
    inputs = iter(["", "帮我找蓝色", "quit"])

    monkeypatch.setattr(
        cli.IntentDialogueAgent,
        "from_config",
        staticmethod(lambda *args, **kwargs: fake_agent),
    )
    monkeypatch.setattr(builtins, "input", lambda prompt: next(inputs))

    await cli._chat("skills", trace_enabled=False)

    assert fake_agent.queries == ["帮我找蓝色"]
    output = capsys.readouterr().out
    assert "请输入 query，或输入 :q 结束。" in output
    assert "意图决策" in output
    assert "对话结束。" in output
