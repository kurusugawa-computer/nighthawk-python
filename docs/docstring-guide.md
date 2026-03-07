# Docstring Guide

This guide defines the docstring conventions for the Nighthawk codebase.

## Style

Use [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings) docstrings. This is the default style parsed by mkdocstrings.

## Scope

Write docstrings only for public API members exported via `__all__` in `src/nighthawk/__init__.py`, plus public classes and functions in these modules:

- `nighthawk.errors`
- `nighthawk.configuration`
- `nighthawk.backends.claude_code`
- `nighthawk.backends.codex`

Do not write docstrings for names prefixed with `_` (module-private).

## Structure

### Summary line

Required. Use imperative mood ("Return ...", "Start ...", "Register ..."). Keep it on one line.

### Sections

| Section | When to include |
|---|---|
| `Args` | One or more parameters exist. Do not repeat type information already present in annotations. |
| `Returns` / `Yields` | Return value is not `None`, or carries meaning that is not obvious from the type alone. |
| `Raises` | The function raises exceptions that callers are expected to catch. |
| `Example` | Required for decorators (`natural_function`, `tool`) and context managers (`run`, `scope`). Optional for everything else. |

### Classes

Write the docstring on the class body, not on `__init__`. mkdocstrings merges `__init__` parameters automatically (`merge_init_into_class: true`).

For Pydantic models, describe fields in an `Attributes` section:

```python
class StepExecutorConfiguration(BaseModel):
    """Configuration for a step executor.

    Attributes:
        model: Model identifier in "provider:model" format (e.g. "openai:gpt-4o").
        prompts: Prompt templates for step execution.
        context_limits: Token and item limits for context rendering.
    """
```

## What not to write

- Do not duplicate type annotations in `Args` descriptions.
- Do not add docstrings just to satisfy a linter; omit them when the signature alone is self-explanatory.
- Do not document internal implementation details in public docstrings.

## Full example

```python
def run(
    step_executor: StepExecutor,
    *,
    run_id: str | None = None,
) -> Iterator[None]:
    """Start an execution run with the given step executor.

    Establishes a run-scoped context that makes the step executor
    available to all Natural blocks executed within this scope.

    Args:
        step_executor: The step executor to use for Natural block execution.
        run_id: Optional identifier for the run. If not provided, a UUID is
            generated automatically.

    Yields:
        None

    Raises:
        RuntimeError: If called while another run is already active.

    Example:
        ```python
        executor = AgentStepExecutor.from_configuration(
            configuration=StepExecutorConfiguration(model="openai-responses:gpt-5-mini"),
        )
        with nighthawk.run(executor):
            result = my_natural_function()
        ```
    """
```
