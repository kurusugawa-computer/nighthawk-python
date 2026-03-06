# Nighthawk

<div align="center">
<img src="assets/nighthawk_logo-128x128.png" alt="logo" width="128px">
</div>

Nighthawk is an experimental Python library exploring a clear separation:

- Use **hard control** (Python code) for strict procedure, verification, and deterministic flow.
- Use **soft reasoning** (an LLM) for semantic interpretation inside small embedded "Natural blocks".

This repository is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Quickstart

Prerequisites: git, uv, Python 3.13+

```bash
git clone https://github.com/kurusugawa-computer/nighthawk-python.git
cd nighthawk-python
uv sync --extra openai
```

Create a `.env` file with your API key (gitignored by default):

```bash
OPENAI_API_KEY=sk-xxxxxxxxx
```

Minimal example:

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

## Natural blocks

A Natural block is a Python docstring or a standalone string literal whose underlying string value begins with `natural\n`.

Bindings:

- `<name>` is a read binding.
- `<:name>` is a write binding.

Write bindings control which values are committed back into Python locals at Natural block boundaries.

Interpolation:

- Natural blocks are literal by default.
- Interpolation is opt-in via inline f-string Natural blocks only (standalone f-string expression statements).
- Docstring Natural blocks are always literal.
- For f-string blocks, brace escaping follows Python rules: write `{{` / `}}` to produce literal `{` / `}`.

## What Nighthawk is trying to prove

Nighthawk is a research vehicle. The main validation goals are:

1. Hard control + soft reasoning works in practice
2. Reduce "LLM is a black box" by mapping state into the interpreter
3. Constrain and validate updates at boundaries
4. Explore alternative workflow styles (Nightjar vs Skills-style)

## References

- Nightjar (upstream concept): <https://github.com/psg-mit/nightjarpy>
