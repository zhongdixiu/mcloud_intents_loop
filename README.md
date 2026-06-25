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

Evaluator can reuse the same model by default. To run the evaluator with a
separate model, set:

```bash
export AGENTSCOPE_EVALUATOR_MODEL_PROVIDER=dashscope
export AGENTSCOPE_EVALUATOR_MODEL_NAME=qwen-plus
```

## SDK

Single-turn routing:

```python
import asyncio
from intent_router import IntentRouter


async def main() -> None:
    router = IntentRouter.from_config(skills_path="skills")
    result = await router.route("帮我找上个月北京拍的猫照片")
    print(result.model_dump_json(ensure_ascii=False, indent=2))


asyncio.run(main())
```

Multi-turn dialogue routing:

```python
import asyncio
from intent_router import IntentDialogueAgent


async def main() -> None:
    agent = IntentDialogueAgent.from_config(skills_path="skills")
    first = await agent.send("帮我找一下")
    print(first.model_dump_json(ensure_ascii=False, indent=2))

    second = await agent.send("打开文件入口")
    print(second.model_dump_json(ensure_ascii=False, indent=2))


asyncio.run(main())
```

## CLI

Single-turn routing:

```bash
python -m intent_router route "帮我找上个月北京拍的猫照片" --skills skills
```

Interactive multi-turn routing:

```bash
python -m intent_router chat --skills skills
```

The `chat` command keeps dialogue history in the terminal process. The router
injects the latest 5 turns as intent-decision context only: user query plus the
final route result. In model prompts, the current input is named
`current_user_query`, and historical route results are injected as
`dialogue_history[].assistant_result`. It does not inject business execution
results or treat historical search results as real file/image/mail handles.
Each turn prints the user query, final route result, `loop_count`, and
`correction_scopes`.
Shortcuts are available in the prompt:

- `:q`, `:quit`, `exit`, `quit`: end the dialogue
- `:h`, `:help`: show help
- `:history`: show dialogue history
- `:clear`: clear dialogue history

## Tests

The test suite uses a fake structured model client, so it does not require a real API key:

```bash
python -m pytest
```
