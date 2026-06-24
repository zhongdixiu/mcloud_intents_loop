from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any, Protocol, TypeVar

from dotenv import load_dotenv

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

def _load_env_files() -> None:
    package_dir = Path(__file__).resolve().parent
    project_dir = package_dir.parent
    for env_path in (project_dir / ".env", package_dir / ".env"):
        if env_path.exists():
            load_dotenv(env_path, override=False)


_load_env_files()

class StructuredModelClient(Protocol):
    async def structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        ...


class AgentScopeStructuredClient:
    """Thin adapter around AgentScope 2.x chat models."""

    def __init__(self, model: Any) -> None:
        self.model = model

    @classmethod
    def from_env(
        cls,
        *,
        provider_env: str = "AGENTSCOPE_MODEL_PROVIDER",
        model_env: str = "AGENTSCOPE_MODEL_NAME",
    ) -> "AgentScopeStructuredClient":
        provider = os.getenv(provider_env, "dashscope").lower()
        model_name = os.getenv(model_env, "qwen-plus")

        if provider == "dashscope":
            from agentscope.credential import DashScopeCredential
            from agentscope.model import DashScopeChatModel

            model = DashScopeChatModel(
                credential=DashScopeCredential(
                    api_key=_required_env("DASHSCOPE_API_KEY"),
                ),
                model=model_name,
                stream=False,
            )
            return cls(model)

        if provider == "openai":
            from agentscope.credential import OpenAICredential
            from agentscope.model import OpenAIChatModel

            model = OpenAIChatModel(
                credential=OpenAICredential(api_key=_required_env("OPENAI_API_KEY")),
                model=model_name,
                stream=False,
            )
            return cls(model)

        raise ValueError(f"Unsupported AGENTSCOPE_MODEL_PROVIDER: {provider}")

    @classmethod
    def evaluator_from_env_if_configured(cls) -> "AgentScopeStructuredClient | None":
        if not (
            os.getenv("AGENTSCOPE_EVALUATOR_MODEL_PROVIDER")
            or os.getenv("AGENTSCOPE_EVALUATOR_MODEL_NAME")
        ):
            return None
        return cls.from_env(
            provider_env="AGENTSCOPE_EVALUATOR_MODEL_PROVIDER",
            model_env="AGENTSCOPE_EVALUATOR_MODEL_NAME",
        )

    async def structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        from agentscope.message import Msg, TextBlock, UserMsg

        messages: list[Any] = [
            Msg(
                name="system",
                role="system",
                content=[TextBlock(text=system_prompt)],
            ),
            UserMsg(name="user", content=user_prompt),
        ]
        response = await self.model.generate_structured_output(
            messages=messages,
            structured_model=response_model,
        )
        content = response.content
        if isinstance(content, response_model):
            return content
        return response_model.model_validate(content)


def _required_env(name: str) -> str:
    value = os.getenv(name)
    if value:
        return value
    raise RuntimeError(
        f"Missing required environment variable {name}. "
        "Set it in the shell, the project .env file, or intent_router/.env.",
    )


class FakeStructuredClient:
    def __init__(self, responses: list[BaseModel | dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, type[BaseModel]]] = []

    async def structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[T],
    ) -> T:
        self.calls.append((system_prompt, user_prompt, response_model))
        if not self.responses:
            raise RuntimeError("FakeStructuredClient has no remaining responses")
        response = self.responses.pop(0)
        if inspect.isclass(response_model) and isinstance(response, response_model):
            return response
        return response_model.model_validate(response)
