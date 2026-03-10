# Providers

Nighthawk delegates Natural block execution to an LLM. The model is selected through the `model` field of `StepExecutorConfiguration` using the `provider:model` format:

```python
from nighthawk.configuration import StepExecutorConfiguration

configuration = StepExecutorConfiguration(model="openai-responses:gpt-5-nano")
```

The default model is `openai-responses:gpt-5-nano`.

There are two categories of providers:

| Category | Examples | How it works |
|---|---|---|
| Pydantic AI provider | OpenAI, Anthropic, Bedrock, Vertex AI, Groq, Mistral, ... | Model identifier passed directly to Pydantic AI |
| Nighthawk backend | Claude Code, Codex | Custom `Model` implementation in Nighthawk |

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

Install the provider dependencies that Pydantic AI requires. Nighthawk offers convenience extras for common providers:

```bash
pip install nighthawk[openai]     # installs pydantic-ai-slim[openai]
pip install nighthawk[vertexai]   # installs pydantic-ai-slim[google,vertexai]
```

For providers without a Nighthawk extra, install the Pydantic AI extra directly:

```bash
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

## Nighthawk backends

The `claude-code` and `codex` providers are Nighthawk-specific backends. They implement the Pydantic AI `Model` protocol internally and expose Nighthawk tools to the underlying CLI via MCP.

### Claude Code backend

The `claude-code` backend uses the Claude Agent SDK to delegate Natural block execution to Claude Code.

```python
configuration = StepExecutorConfiguration(model="claude-code:default")
```

Environment:

- Requires `ANTHROPIC_API_KEY` or an active Claude Code session.
- Nighthawk tools are exposed to Claude Code via an embedded MCP server.
- Tool names are prefixed with `mcp__nighthawk__` in the Claude Code environment.

Install the Nighthawk extra:

```bash
pip install nighthawk[claude-code]
```

Settings are controlled via `ClaudeCodeModelSettings`:

```python
configuration = StepExecutorConfiguration(
    model="claude-code:default",
    model_settings={
        "permission_mode": "bypassPermissions",
        "claude_max_turns": 50,
        "working_directory": "/path/to/project",
    },
)
```

### Codex backend

The `codex` backend runs Codex as a subprocess and communicates via an embedded MCP tool server.

```python
configuration = StepExecutorConfiguration(model="codex:default")
```

Environment:

- Requires `CODEX_API_KEY`.
- An HTTP MCP server is started in a background thread on a random local port.
- The Codex CLI connects to this server to access Nighthawk tools.

Install the Nighthawk extra:

```bash
pip install nighthawk[codex]
```

## Custom backends

Nighthawk's step executor protocol allows custom backends. Implement either `SyncStepExecutor` or `AsyncStepExecutor`:

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
        # Implement provider-specific execution
        ...
```

Alternatively, wrap a Pydantic AI `Agent` using `AgentStepExecutor.from_agent(...)`:

```python
from pydantic_ai import Agent
from nighthawk.runtime.step_executor import AgentStepExecutor

agent = Agent(model="openai-responses:gpt-5-nano", ...)
executor = AgentStepExecutor.from_agent(agent=agent)
```
