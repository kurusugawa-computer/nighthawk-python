# Nighthawk Quickstart

This quickstart focuses on the shortest path to running your first Natural block.

## Setup

Prerequisites: Python 3.13+. Nighthawk assumes Natural DSL sources are trusted, repository-managed assets (see [Design](design.md#3-hard-constraints)).

Install Nighthawk and a provider:

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

For other providers, see [Providers](providers.md).

## First Example

Save as `quickstart.py`:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini")
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

This example uses `gpt-5.4-mini` for higher quality. The library default is `gpt-5.4-nano` (see [Providers](providers.md)).

Run with your API key:

```bash
export OPENAI_API_KEY=sk-xxxxxxxxx
python quickstart.py
# => 20
```

## Bindings at a Glance

- `<name>` — read binding. The value is visible inside the Natural block. Mutable objects can be mutated in-place.
- `<:name>` — write binding. The LLM can set a new value, which is committed back into Python locals.

## Coding Agent Backend Example

Nighthawk can also delegate Natural blocks to a coding agent CLI. Save as `quickstart_cli.py`:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="claude-code-cli:default")
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

Install the backend extra and the Claude Code CLI (a system tool, not a Python package):

```bash
pip install nighthawk-python[claude-code-cli]
claude auth login
python quickstart_cli.py
# => 20
```

The Claude Code CLI must be installed separately. See [Coding agent backends](coding-agent-backends.md) for installation, configuration, and skills.

## Step Executor

Natural functions require a step executor, created via `AgentStepExecutor.from_configuration()` and activated with `with nh.run(step_executor):`. The `model` field uses the `provider:model` identifier format from [Pydantic AI](https://ai.pydantic.dev/models/overview/); see [Providers](providers.md) for the full list. See the [Tutorial](tutorial.md#step-executor) for details.

## Credentials

Set `OPENAI_API_KEY` for OpenAI models (used in the first example above). For other providers, see [Providers](providers.md). For coding agent backends, see [Coding agent backends](coding-agent-backends.md).

## Troubleshooting

**`NighthawkError: StepExecutor is not set`**

Natural functions must be called inside a `with nh.run(step_executor):` context. Ensure your call site is wrapped in a run context.

**`ValueError: Invalid model identifier`**

The model identifier must be in `provider:model` format (e.g., `openai-responses:gpt-5.4-mini`). Check for typos or missing provider prefix.

**`OPENAI_API_KEY` not set**

Set the environment variable before running: `export OPENAI_API_KEY=sk-xxxxxxxxx`. For other providers, see the credentials section above.

**`ModuleNotFoundError` for a provider**

Install the required provider package. For Pydantic AI providers: `pip install pydantic-ai-slim[openai]`. For coding agent backends: `pip install nighthawk-python[claude-code-cli]`.
