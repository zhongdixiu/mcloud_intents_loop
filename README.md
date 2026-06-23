# mCloud Intent Router

AgentScope-based intent routing agent for the `skills/*.md` intent definitions.

## Requirements

- Python 3.11+
- `agentscope>=2.0.2,<2.1`
- `pydantic`
- `PyYAML`

The current implementation targets AgentScope 2.x APIs:

- `DashScopeChatModel(..., stream=False)`
- `OpenAIChatModel(..., stream=False)`
- `model.generate_structured_output(messages=..., structured_model=...)`

## Model Configuration

DashScope is the default provider:

```bash
export AGENTSCOPE_MODEL_PROVIDER=dashscope
export AGENTSCOPE_MODEL_NAME=qwen-plus
export DASHSCOPE_API_KEY=...
```

OpenAI-compatible usage:

```bash
export AGENTSCOPE_MODEL_PROVIDER=openai
export AGENTSCOPE_MODEL_NAME=gpt-4.1
export OPENAI_API_KEY=...
```

## SDK

```python
import asyncio
from intent_router import IntentRouter


async def main() -> None:
    router = IntentRouter.from_config(skills_path="skills")
    result = await router.route("帮我找上个月北京拍的猫照片")
    print(result.model_dump_json(ensure_ascii=False, indent=2))


asyncio.run(main())
```

## CLI

```bash
python -m intent_router route "帮我找上个月北京拍的猫照片" --skills skills
```

If the result status is `clarify`, pass the returned `resume_token` with the user's clarification:

```bash
python -m intent_router route "搜索云盘里的照片" --resume-token "..." --skills skills
```

## Tests

The test suite uses a fake structured model client, so it does not require a real API key:

```bash
python -m pytest
```

