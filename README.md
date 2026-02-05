# Nighthawk

Nighthawk is an experimental Python library exploring a simple split:

- Use **hard control** (Python code) for strict procedure, verification, and deterministic flow.
- Use **soft reasoning** (an LLM) for semantic interpretation inside small embedded "Natural blocks".

This repository is a compact reimplementation of the core ideas of [Nightjar](https://github.com/psg-mit/nightjarpy).

## Read this first

- Specification (canonical): `docs/design.md`
- Roadmap (future only): `docs/roadmap.md`

## Quick start (Agent mode)

Nighthawk executes Natural blocks via an LLM. To run any LLM-backed execution you must provide provider credentials.

We expect a local `.env` file to exist with an API key entry like:

```bash
OPENAI_API_KEY=sk-xxxxxxxxx
```

- Do not commit `.env` (it is gitignored by default).
- This README does not prescribe how `.env` is loaded; ensure your environment exports `OPENAI_API_KEY` when running.

Prereqs:

- git
- uv
- Python 3.13+

Run:

```bash
git clone https://github.com/kurusugawa-computer/nighthawk-python.git
cd nighthawk-python
uv sync
NIGHTHAWK_RUN_INTEGRATION_TESTS=1 uv run pytest -q tests/integration/test_llm_integration.py
```

## What is in this repository

- Package: `src/nighthawk/`
- Tests: `tests/`
- Design/spec docs: `docs/`

Constraints / defaults (current implementation):

- Supported Python version: 3.13+ (by design).
- Default model: `openai-responses:gpt-5-nano`.
- Recommended model (quality): `openai-responses:gpt-5.2`.
- Optional backends (extras):
  - `openai`: `pip install "nighthawk[openai] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`
  - `vertexai`: `pip install "nighthawk[vertexai] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`
  - `claude-code`: `pip install "nighthawk[claude-code] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`
  - `codex`: `pip install "nighthawk[codex] @ git+https://github.com/kurusugawa-computer/nighthawk-python"`

Model identifiers:

- `ExecutionConfiguration(model=...)` uses `provider:model`.
- For `claude-code` and `codex`, you can use `:default` to use the backend/provider default model.
  - Examples: `claude-code:default`, `codex:default`.
- `:outside` is not supported.

## What Nighthawk is trying to prove

Nighthawk is a research vehicle. The main validation goals are:

1. Hard control + soft reasoning works in practice
    - Keep loops, conditionals, data plumbing, and "must run exactly N times" logic in Python.
    - Delegate semantic interpretation to Natural blocks.

2. Reduce "LLM is a black box" by mapping state into the interpreter
    - Treat the Python interpreter as the primary external memory.
    - Make intermediate state visible as Python locals / structured objects rather than hidden chat history.

3. Constrain and validate updates at boundaries
    - Use explicit output bindings (e.g., `<:result>`) so the LLM can only commit specific values.
    - Optionally use a typed memory model (Pydantic) to force a domain mental model and validate updates.

4. Explore alternative workflow styles (Nightjar vs Skills-style)
    - Natural-language-first workflows are attractive, but require solving state synchronization between natural language and code.
    - Nighthawk starts from the Nightjar side and explores how far we can push interpreter-visible state mapping.

## Natural blocks (practical minimum)

A Natural block is a Python docstring or a standalone string literal whose underlying string value begins with:

- `natural\n`

Bindings:

- `<name>` is an input binding.
- `<:name>` is an output binding.

Output bindings control which values are committed back into Python locals at Natural block boundaries.

Note: Natural blocks are literal by default. Interpolation is opt-in via inline f-string Natural blocks only (standalone f-string expression statements). Docstring Natural blocks are always literal. For interpolated (f-string) inline blocks, brace escaping follows Python f-string rules: write `{{` / `}}` in the f-string source to produce literal `{` / `}`.

## Workflow styles (hardness vs flexibility)

This section summarizes the tradeoffs in terms of "hard control" vs "flexibility".

### 1) Nightjar style (hard control, embedded Natural blocks)

You write strict flow in Python, and embed Natural blocks where semantics are needed.

Pros:
- Hard guarantees: exact loops, strict conditionals, deterministic boundaries.
- Tools: debuggers, tests, linters, and normal software engineering practices apply.
- The LLM is "physically constrained" to operate on interpreter-visible objects (locals, memory, tool context).

Cons:
- Knowledge often ends up encoded in code-adjacent artifacts, which can be less maintainable by non-engineers.

Pseudo-code example:

```py
@nj.fn
def calculate_average(numbers):
    """natural
    Consider the values of <numbers> and compute the semantic average as <:result>
    """
    return result

result = calculate_average([1, "2", "three", "cuatro", "五"])
print(result)  # 3.0
```

### 2) Skills-style / reverse Nightjar (flexible workflow, code snippets as needed)

You write a natural language workflow first, and embed code only where strict procedures are needed.

Pros:
- Excellent for strategy, iteration, and human collaboration.
- Similar spirit to literate programming: readable narrative with precise code where necessary.

Cons:
- The hard part is state synchronization: how to share and reconcile execution state between
  - the natural language plan/world, and
  - the code execution world.

Reference (packaging convention example): https://zenn.dev/kotapon/articles/64982726eea408

Pseudo-code example:

````md
Compute the "semantic average" of the target list using the following function.
However, the target list contains mixed numeric representations, so convert the elements appropriately before calling the function and passing them as the argument.

```py
def calculate_average(numbers):
    return sum(numbers) / len(numbers)
```

Target list: `[1, "2", "three", "cuatro", "五"]`
````

### 3) Hybrid nesting (Python -> Natural -> Python -> ...)

Nighthawk's execution model is Python-first alternation: Python controls the steps, and Natural blocks are inserted where semantic interpretation is needed.

Pseudo-code example:

```py
def python_average(numbers):
    return sum(numbers) / len(numbers)

@nh.fn
def calculate_average(numbers):
    """natural
    Normalize <numbers> into python number list (e.g., [1, 2, ...]).
    Then compute <:result> by calling <python_average>.
    """
    return result

calculate_average([1, "2", "three", "cuatro", "五"])  # 3.0
```

## Safety model

This project assumes the Natural DSL source and any imported markdown are trusted, repository-managed assets.

Do not feed user-generated content (web forms, chat logs, CLI input, database text, external API responses) into Natural blocks or any host-side interpolation helpers you define.

## References

- Nightjar (upstream concept): https://github.com/psg-mit/nightjarpy
- Agent Skills (external article): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
