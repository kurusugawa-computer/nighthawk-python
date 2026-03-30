# Pydantic AI providers

> See [Executors](executors.md) for choosing between providers, backends, and custom executors.

Nighthawk delegates Natural block execution to an LLM. The model is selected through the `model` field of `StepExecutorConfiguration` using the `provider:model` format:

```py
import nighthawk as nh

configuration = nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-nano")
```

The default model is `openai-responses:gpt-5.4-nano`. Recommended model for quality: `openai-responses:gpt-5.4`.

## Pydantic AI providers

Any provider that [Pydantic AI supports](https://ai.pydantic.dev/models/overview/) works with Nighthawk. The model identifier is passed directly to a Pydantic AI `Agent` -- Nighthawk has no provider-specific code for these.

Examples:

OpenAI:
```py
configuration = nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-nano")
```

Anthropic (direct API):
```py
configuration = nh.StepExecutorConfiguration(model="anthropic:claude-sonnet-4-6")
```

AWS Bedrock:
```py
configuration = nh.StepExecutorConfiguration(model="bedrock:us.anthropic.claude-sonnet-4-6-v1:0")
```

Google Vertex AI:
```py
configuration = nh.StepExecutorConfiguration(model="google-vertex:gemini-3-pro-preview")
```

Groq:
```py
configuration = nh.StepExecutorConfiguration(model="groq:llama-4-scout-17b-16e-instruct")
```


### Installation

Install the provider dependencies that Pydantic AI requires:

OpenAI:
```bash
pip install pydantic-ai-slim[openai]
```

Anthropic (direct API):
```bash
pip install pydantic-ai-slim[anthropic]
```

AWS Bedrock:
```bash
pip install pydantic-ai-slim[bedrock]
```

Google Vertex AI:
```bash
pip install pydantic-ai-slim[google,vertexai]
```

Groq:
```bash
pip install pydantic-ai-slim[groq]
```

See the [Pydantic AI documentation](https://ai.pydantic.dev/models/overview/) for the full list of providers, required extras, and credential setup.

Nighthawk transparently forwards all provider-specific configuration (temperature, top_p, streaming, tool_choice, etc.) to Pydantic AI via `model_settings`. Because provider-specific options are numerous and vary across providers, Nighthawk does not document them individually -- refer to the [Pydantic AI documentation](https://ai.pydantic.dev/models/overview/) for provider-specific settings.

### Model settings

Pydantic AI providers accept standard Pydantic AI model settings via the `model_settings` field:

```py
configuration = nh.StepExecutorConfiguration(
    model="openai-responses:gpt-5.4-nano",
    model_settings={"temperature": 0.5},
)
```

## Troubleshooting

**`ModuleNotFoundError` for a provider**

Install the required provider package. For example: `pip install pydantic-ai-slim[openai]`. See the [installation section](#installation) above for all provider extras.

**`ValueError: Invalid model identifier`**

The model identifier must be in `provider:model` format (e.g., `openai-responses:gpt-5.4-mini`). Check for typos or a missing provider prefix. See the [Pydantic AI documentation](https://ai.pydantic.dev/models/overview/) for valid provider prefixes.

**Provider authentication errors**

Each Pydantic AI provider requires its own credentials (e.g., `OPENAI_API_KEY` for OpenAI). Nighthawk does not manage provider credentials -- see the [Pydantic AI documentation](https://ai.pydantic.dev/models/overview/) for provider-specific credential setup.
