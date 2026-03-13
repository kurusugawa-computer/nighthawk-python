# Providers

Nighthawk delegates Natural block execution to an LLM. The model is selected through the `model` field of `StepExecutorConfiguration` using the `provider:model` format:

```python
from nighthawk.configuration import StepExecutorConfiguration

configuration = StepExecutorConfiguration(model="openai-responses:gpt-5-nano")
```

The default model is `openai-responses:gpt-5-nano`. Recommended model for quality: `openai-responses:gpt-5.4`.

## Choosing a provider

There are three ways to connect Nighthawk to a model:

| Approach | When to use |
|---|---|
| Pydantic AI provider | Broad model choice, standard Pydantic AI configuration |
| Coding agent backend | Skill execution, project-scoped files, or MCP tool exposure |
| Custom backend | Full control over step execution |

### Capability matrix

| Capability | Pydantic AI provider | Coding agent backend | Custom backend |
|---|---|---|---|
| Natural block execution | Yes | Yes | Yes |
| Skill execution | No | Yes | Depends on implementation |
| MCP tool exposure | No | Yes (automatic) | Depends on implementation |
| Project-scoped files (CLAUDE.md, AGENTS.md) | No | Yes | Depends on implementation |
| Model settings | Pydantic AI standard | Backend-specific | User-defined |

## Pydantic AI providers

Any provider that [Pydantic AI supports](https://ai.pydantic.dev/models/overview/) works with Nighthawk. The model identifier is passed directly to a Pydantic AI `Agent` -- Nighthawk has no provider-specific code for these.

Examples:

```python
# OpenAI
configuration = StepExecutorConfiguration(model="openai-responses:gpt-5-nano")

# Anthropic (direct API)
configuration = StepExecutorConfiguration(model="anthropic:claude-sonnet-4-6")

# AWS Bedrock
configuration = StepExecutorConfiguration(model="bedrock:us.anthropic.claude-sonnet-4-6-v1:0")

# Google Vertex AI
configuration = StepExecutorConfiguration(model="google-vertex:gemini-3-pro-preview")

# Groq
configuration = StepExecutorConfiguration(model="groq:llama-4-scout-17b-16e-instruct")
```

### Installation

Install the provider dependencies that Pydantic AI requires:

```bash
pip install pydantic-ai-slim[openai]
pip install pydantic-ai-slim[google,vertexai]
pip install pydantic-ai-slim[anthropic]
pip install pydantic-ai-slim[bedrock]
pip install pydantic-ai-slim[groq]
```

See the [Pydantic AI documentation](https://ai.pydantic.dev/models/overview/) for the full list of providers, required extras, and credential setup.

### Model settings

Pydantic AI providers accept standard Pydantic AI model settings via the `model_settings` field:

```python
configuration = StepExecutorConfiguration(
    model="openai-responses:gpt-5-nano",
    model_settings={"temperature": 0.5},
)
```

## Coding agent backends

The `claude-code-sdk`, `claude-code-cli`, and `codex` backends implement the Pydantic AI `Model` protocol internally but delegate inference to a coding agent CLI rather than a Pydantic AI provider. Install with `nighthawk[claude-code-sdk]`, `nighthawk[claude-code-cli]`, or `nighthawk[codex]`. See [Coding agent backends](coding-agent-backends.md) for configuration, skill behavior, and backend-specific settings.

## Custom backends

Nighthawk's `SyncStepExecutor` and `AsyncStepExecutor` protocols define the step execution interface. Any object implementing one of these protocols can serve as a backend.

For most cases, wrap a Pydantic AI `Agent` using `AgentStepExecutor`:

```python
from pydantic_ai import Agent
from nighthawk.runtime.step_executor import AgentStepExecutor

agent = Agent(model="openai-responses:gpt-5-nano", ...)
executor = AgentStepExecutor.from_agent(agent=agent)
```

For full control, implement `AsyncStepExecutor` (or `SyncStepExecutor` for synchronous use) directly:

```python
from nighthawk.runtime.step_executor import AsyncStepExecutor
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_contract import StepOutcome

class MyBackend:
    async def run_step_async(
        self,
        *,
        processed_natural_program: str,
        step_context: StepContext,
        binding_names: list[str],
        allowed_step_kinds: tuple[str, ...],
    ) -> tuple[StepOutcome, dict[str, object]]:
        ...
```

See the [Pydantic AI Model documentation](https://ai.pydantic.dev/models/overview/) for extending `Agent` with custom models, and the [Pydantic AI tools documentation](https://ai.pydantic.dev/tools/) for tool integration patterns.
