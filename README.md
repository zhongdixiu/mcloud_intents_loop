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

The router has a single routing philosophy for SDK, CLI, and Excel evaluation:
predict the most likely `skill / intent / code`. Missing entities, resource
handles, operation objects, and parameters do not trigger clarification or block
intent-code prediction.

The main chain is:

```text
Contextualizer -> Top-N skill candidates -> Top-M intent candidates
-> candidate-set evaluator rerank -> optional targeted expansion -> switch gate
```

The evaluator can only select an existing `candidate_id` or request one targeted
candidate expansion round. It cannot invent a new code. The controller applies a
switch gate before replacing the selector's first candidate, so weak evaluator
signals are recorded in diagnostics instead of overriding strong candidates.

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
first injects the latest 5 turns into a contextualizer prompt: user query plus
the final route result. The contextualizer produces `resolved_query` and a
structured `semantic_frame`; skill candidate generation, intent candidate
generation, and evaluator rerank all use that unified request. Later prompts
keep `current_user_query` only for audit context and do not receive
`dialogue_history` directly. Business execution results and historical no-match
reasons are not injected as dialogue semantics. Ordinary dialogue is represented
as a normal candidate and final route: `普通对话`, `code="000"`, `skill=null`.
Each turn prints the resolved query, final route result, `loop_count`, and
`correction_scopes`; targeted candidate expansion increments the loop count.
Shortcuts are available in the prompt:

- empty input: ignored, continue waiting for the next query
- `:q`, `:quit`, `exit`, `quit`: end the dialogue
- `:h`, `:help`: show help
- `:history`: show dialogue history
- `:clear`: clear dialogue history

## Tests

The test suite uses a fake structured model client, so it does not require a real API key:

```bash
python -m pytest
```
