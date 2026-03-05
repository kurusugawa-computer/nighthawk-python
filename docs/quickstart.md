# Nighthawk Quickstart

This quickstart focuses on the shortest path to running your first Natural block.

## Setup

- Python `3.13+`
- Install dependencies: `uv sync --all-extras --all-groups`
- Credentials:
  - `OPENAI_API_KEY` for `openai-responses:*`
  - For other backends, see the model identifiers section in README.md.

## One Executable Example

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini")
)

with nh.run(step_executor):

    @nh.natural_function
    def summarize_post(post: str) -> str:
        summary = ""
        """natural
        Read <post> and set <:summary> to a concise summary.
        """
        return summary

    print(summarize_post("Ship the patch by Friday and include migration notes."))
```

## Bindings at a Glance

- `<name>` — read binding. The value is visible inside the Natural block. Mutable objects can be mutated in-place.
- `<:name>` — write binding. Use `nh_assign` to set it; the new value is committed back into Python locals.

## Next Steps

For patterns, techniques, and detailed guidance, see `docs/manual.md`.
