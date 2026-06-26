from __future__ import annotations

import builtins
from pathlib import Path

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


async def test_eval_xlsx_passes_end_to_end_flag(monkeypatch, capsys) -> None:
    captured = {}

    async def fake_evaluate_xlsx_cases(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"output_path": "out.xlsx", "total": 0}

    monkeypatch.setattr(cli, "evaluate_xlsx_cases", fake_evaluate_xlsx_cases)

    await cli._eval_xlsx(
        Path("cases.xlsx"),
        "skills",
        None,
        trace_enabled=False,
        if_end2end=True,
        intent_only=True,
    )

    assert captured["kwargs"]["if_end2end"] is True
    assert captured["kwargs"]["intent_only"] is True
    assert "评测模式: end2end" in capsys.readouterr().out


async def test_eval_format_xlsx_passes_end_to_end_flag(monkeypatch, capsys) -> None:
    captured = {}

    async def fake_evaluate_format_xlsx_cases(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"output_path": "out.xlsx", "total": 0}

    monkeypatch.setattr(
        cli,
        "evaluate_format_xlsx_cases",
        fake_evaluate_format_xlsx_cases,
    )

    await cli._eval_format_xlsx(
        Path("cases.xlsx"),
        "skills",
        None,
        trace_enabled=False,
        if_end2end=True,
        intent_only=True,
    )

    assert captured["kwargs"]["if_end2end"] is True
    assert captured["kwargs"]["intent_only"] is True
    assert "评测模式: end2end" in capsys.readouterr().out
