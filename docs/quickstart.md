# Nighthawk Quickstart

This quickstart focuses on the shortest path to running your first Natural block.

## Setup

Prerequisites: Python 3.13+

Install with the OpenAI backend:

```bash
pip install "nighthawk[openai] @ git+https://github.com/kurusugawa-computer/nighthawk-python"
```

Other available extras: `vertexai`, `claude-code-sdk`, `claude-code-cli`, `codex`. See [Backends and model identifiers](#backends-and-model-identifiers) below.

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
- `<:name>` — write binding. Use `nh_assign` to set it; the new value is committed back into Python locals.

## Backends and model identifiers

Nighthawk uses the `provider:model` identifier format from [Pydantic AI](https://ai.pydantic.dev/models/overview/). For standard Pydantic AI providers, the identifier is passed directly to Pydantic AI.

| Extra | Pydantic AI provider | Example identifier |
|---|---|---|
| `openai` | [OpenAI](https://ai.pydantic.dev/models/openai/) | `openai-responses:gpt-5-mini` |
| `vertexai` | [Google / Vertex AI](https://ai.pydantic.dev/models/gemini/) | `google-vertex:gemini-3-pro-preview` |

Nighthawk-specific backends (not backed by Pydantic AI):

| Extra | Example identifier |
|---|---|
| `claude-code-sdk` | `claude-code-sdk:default` |
| `claude-code-cli` | `claude-code-cli:default` |
| `codex` | `codex:default` |

Default model: `openai-responses:gpt-5-nano`. Recommended model for quality: `openai-responses:gpt-5.4`.

## Credentials

Credential configuration for Pydantic AI providers follows [Pydantic AI conventions](https://ai.pydantic.dev/models/overview/). Common environment variables:

- `OPENAI_API_KEY` — required for OpenAI models ([details](https://ai.pydantic.dev/models/openai/))
- `GOOGLE_API_KEY` — required for Google / Vertex AI models ([details](https://ai.pydantic.dev/models/gemini/))

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

Install the corresponding extra: `pip install "nighthawk[openai]"`, `pip install "nighthawk[claude-code-sdk]"`, etc.

## Next Steps

- **[Tutorial](tutorial.md)** — Learn Nighthawk from first principles.
- **[Design](design.md)** — Canonical specification.
- **[API Reference](api.md)** — Auto-generated API documentation.
