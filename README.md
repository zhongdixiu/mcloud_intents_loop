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

The router first predicts `skill / intent / code`, then extracts parameters only
for the selected business route. Missing parameters never change the selected
route. Missing required parameters produce `clarify`; invalid optional values are
discarded and recorded in diagnostics.

The main chain is:

```text
Contextualizer -> Top-N skill candidates -> Top-M intent candidates
-> parallel intent generation -> candidate validation -> decision gate
-> conditional evaluator / optional targeted expansion -> route gate
-> parameter extractor -> parameter validator
```

High-confidence, low-risk routes skip the evaluator. The evaluator can select an
existing `candidate_id`, request one targeted expansion round, abstain, or report
an unsupported request. A switch requires both evaluator confidence and score
margin thresholds, and cannot bypass blocking risks.

Final statuses are:

- `matched`: route and validated parameters are ready
- `clarify`: route is fixed but required parameters are missing
- `abstain`: available evidence cannot safely distinguish candidates
- `unsupported`: the registered capabilities do not support the request
- `error`: a required routing stage failed

Router behavior is configured with `RouterConfig`:

```python
from intent_router import IntentRouter, RouterConfig

config = RouterConfig()
config.decision.evaluator_mode = "conditional"
config.concurrency.intent_generation_per_request = 3
config.timeouts.route_deadline_ms = 8000

router = IntentRouter.from_config("skills", config=config)
```

Model calls use stage-specific timeouts, retry transient failures with
exponential backoff, respect a route deadline, and expose call records in
`RouteDiagnostics`.

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

Evaluator mode can be selected for routing and evaluation:

```bash
python -m intent_router route "搜索合同" --evaluator-mode conditional
python -m intent_router eval-xlsx --cases cases.xlsx --evaluator-mode disabled
```

Interactive multi-turn routing:

```bash
python -m intent_router chat --skills skills
```

Validate all Skill definitions without calling a model:

```bash
python -m intent_router validate-skills --skills skills
```

Each Skill uses required YAML frontmatter plus the standard `Skill Scope`,
`Intent Routing Principles`, `Intent Contrast Rules`, `Tools Schema`,
`Intent-Specific Rules`, positive/negative examples, and execution sections.
The first-level router receives only the lightweight Skill card. The
second-level router receives the parsed routing context and never the raw
Markdown or execution instructions.

The `chat` command keeps dialogue history in the terminal process. SDK callers
can use `SessionStore` with a `SessionKey(tenant_id, user_id, session_id)`;
`InMemorySessionStore` is included, while Redis/database implementations remain
deployment concerns. Persisted dialogue summaries omit extracted parameters.
The router
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

Excel evaluation supports legacy intent-code columns and optional explicit
`expected_skill_id`, `expected_intent`, `expected_code`, `expected_params`, and
`alternate_routes` columns. It reports full Route Key accuracy, Skill/Intent
recall, evaluator gain/loss and call rate, expansion waste, parameter exact
match, model-call counts, retries/timeouts, and latency percentiles.
