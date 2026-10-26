# Nighthawk quickstart

> Start here to install Nighthawk and run a Natural function.

## Setup

Prerequisites: Python 3.13+. Nighthawk assumes Natural source and included Markdown are trusted program assets authorized for their execution context (see [Specification](specification.md#3-hard-constraints)).

Install Nighthawk and a provider:

```bash
pip install nighthawk-python pydantic-ai-slim[openai]
```

## First example

Save as `quickstart.py`:

```py
import nighthawk as nh


@nh.function
def interpret_quantities(items: str) -> list[int]:
    quantities: list[int] = []
    """execute
    Read <items> and set <:quantities> to the quantity of each item mentioned.
    Interpret number words and expressions such as a dozen.
    """
    return quantities


with nh.run(execution_model="openai-responses:gpt-6-luna"):
    print(sum(interpret_quantities("three apples, a dozen eggs, and 5 oranges")))
```

Run with your API key:

```bash
export OPENAI_API_KEY=sk-xxxxxxxxx
python quickstart.py
# => 20
```

Natural interprets the quantities into a validated `list[int]`; Python computes their sum.
The decorator prepares the function from its source, while `nh.run(...)` selects the model when you call it.
Type validation does not establish whether the interpretation is correct.

In this example, `<items>` is a **read binding** (the LLM can see its value) and `<:quantities>` is a **write binding** (the LLM sets a new value, committed back into Python locals). See [Natural blocks](natural-blocks.md#providing-data-to-a-block) for details on bindings, functions, and composition.

## Troubleshooting

**`NighthawkError: StepExecutor is not set`**

Call the function inside `with nh.run(execution_model="openai-responses:gpt-6-luna"):`.
Decorating the function does not start a run.

**`OPENAI_API_KEY` not set**

Set the environment variable in the shell that runs Python, as shown above.

For other errors (model identifiers, missing provider packages, authentication), see [Pydantic AI providers troubleshooting](pydantic-ai-providers.md#troubleshooting).

For coding agent backends and other execution options, see [Executors](executors.md).

## Next steps

Continue to **[Natural blocks](natural-blocks.md)** to learn about prompt structure, bindings, functions, and writing guidelines.
