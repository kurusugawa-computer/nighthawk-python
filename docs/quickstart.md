# Nighthawk quickstart

> Start here to install Nighthawk and run a Natural function.

## Setup

Prerequisites: Python 3.13+. Nighthawk assumes Natural DSL sources are trusted, repository-managed assets (see [Specification](specification.md#3-hard-constraints)).

Install Nighthawk and a provider:

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

## First example

Save as `quickstart.py`:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.6-luna"))

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

In this example, `<items>` is a **read binding** (the LLM can see its value) and `<:total>` is a **write binding** (the LLM sets a new value, committed back into Python locals). See [Natural blocks](natural-blocks.md#providing-data-to-a-block) for details on bindings, functions, and composition.

## Troubleshooting

**`NighthawkError: StepExecutor is not set`**

Run the function inside `with nh.run(step_executor):`, as in the example.

**`OPENAI_API_KEY` not set**

Set the environment variable in the shell that runs Python, as shown above.

For other errors (model identifiers, missing provider packages, authentication), see [Pydantic AI providers troubleshooting](pydantic-ai-providers.md#troubleshooting).

For coding agent backends and other execution options, see [Executors](executors.md).

## Next steps

Continue to **[Natural blocks](natural-blocks.md)** to learn about prompt structure, bindings, functions, and writing guidelines.
