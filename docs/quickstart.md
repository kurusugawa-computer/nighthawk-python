# Nighthawk Quickstart

This quickstart focuses on the shortest path to running your first Natural block.

## Setup

Prerequisites: Python 3.13+

Install Nighthawk and a provider:

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

For other providers, see [Backends and model identifiers](#backends-and-model-identifiers) below.

## First Example

Save as `quickstart.py`:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini")
)

with nh.run(step_executor):

    @nh.natural_function
    def calculate_total(items: str) -> int:
        total = 0
        """natural
        Read <items> and set <:total> to the sum of all quantities mentioned.
        """
        return total

    print(calculate_total("three apples, a dozen eggs, and 5 oranges"))
```

Run with your API key:

```bash
export OPENAI_API_KEY=sk-xxxxxxxxx
python quickstart.py
# => 20
```

## Bindings at a Glance

- `<name>` — read binding. The value is visible inside the Natural block. Mutable objects can be mutated in-place.
- `<:name>` — write binding. The LLM can set a new value, which is committed back into Python locals.

## Backends and model identifiers

Nighthawk uses the `provider:model` identifier format from [Pydantic AI](https://ai.pydantic.dev/models/overview/). For standard Pydantic AI providers, the identifier is passed directly to Pydantic AI.

| Pydantic AI provider | Install | Example identifier |
|---|---|---|
| [OpenAI](https://ai.pydantic.dev/models/openai/) | `pip install pydantic-ai-slim[openai]` | `openai-responses:gpt-5-mini` |
| [Google / Vertex AI](https://ai.pydantic.dev/models/gemini/) | `pip install pydantic-ai-slim[google,vertexai]` | `google-vertex:gemini-3-pro-preview` |
| [Anthropic](https://ai.pydantic.dev/models/anthropic/) | `pip install pydantic-ai-slim[anthropic]` | `anthropic:claude-sonnet-4-6` |
| [AWS Bedrock](https://ai.pydantic.dev/models/bedrock/) | `pip install pydantic-ai-slim[bedrock]` | `bedrock:us.anthropic.claude-sonnet-4-6-v1:0` |
| [Groq](https://ai.pydantic.dev/models/groq/) | `pip install pydantic-ai-slim[groq]` | `groq:llama-4-scout-17b-16e-instruct` |

Nighthawk-specific backends (not backed by Pydantic AI):

| Extra | Install | Example identifier |
|---|---|---|
| `claude-code-sdk` | `pip install nighthawk-python[claude-code-sdk]` | `claude-code-sdk:default` |
| `claude-code-cli` | `pip install nighthawk-python[claude-code-cli]` | `claude-code-cli:default` |
| `codex` | `pip install nighthawk-python[codex]` | `codex:default` |

See [Providers](providers.md) for the default and recommended models.

## Credentials

Credential configuration for Pydantic AI providers follows [Pydantic AI conventions](https://ai.pydantic.dev/models/overview/). Common environment variables:

- `OPENAI_API_KEY` — required for OpenAI models ([details](https://ai.pydantic.dev/models/openai/))
- `GOOGLE_API_KEY` — required for Google AI (Gemini API) models ([details](https://ai.pydantic.dev/models/gemini/))
- Google Vertex AI uses Application Default Credentials, not an API key ([details](https://ai.pydantic.dev/models/gemini/#vertex-ai))

## Safety model

Nighthawk assumes the Natural DSL source and any imported markdown are trusted, repository-managed assets.

Do not feed user-generated content (web forms, chat logs, CLI input, database text, external API responses) into Natural blocks or any host-side interpolation helpers you define.

## Troubleshooting

**`NighthawkError: StepExecutor is not set`**

Natural functions must be called inside a `with nh.run(step_executor):` context. Ensure your call site is wrapped in a run context.

**`ValueError: Invalid model identifier`**

The model identifier must be in `provider:model` format (e.g., `openai-responses:gpt-5-mini`). Check for typos or missing provider prefix.

**`OPENAI_API_KEY` not set**

Set the environment variable before running: `export OPENAI_API_KEY=sk-xxxxxxxxx`. For other providers, see the credentials section above.

**`ModuleNotFoundError` for a provider**

Install the required provider package. For Pydantic AI providers: `pip install pydantic-ai-slim[openai]`. For coding agent backends: `pip install nighthawk-python[claude-code-sdk]`.

## Next Steps

- **[Tutorial](tutorial.md)** — Learn Nighthawk from first principles.
- **[Providers](providers.md)** — LLM providers and configuration.
- **[Coding agent backends](coding-agent-backends.md)** — Claude Code and Codex backend configuration.
- **[Design](design.md)** — Canonical specification.
- **[API Reference](api.md)** — Auto-generated API documentation.
- **[Roadmap](roadmap.md)** — Future directions.
- **[For coding agents](for-coding-agents.md)** — Nighthawk development guide for coding agents (LLM reference).
