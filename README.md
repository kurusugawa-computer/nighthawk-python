# Nighthawk

Nighthawk is an experimental Python library exploring a simple split:

- Use **hard control** (Python code) for strict procedure and verification.
- Use **soft reasoning** (LLM) for semantic interpretation inside small embedded "Natural blocks".

This is a compact reimplementation of the core ideas of Nightjar.

Reference (upstream concept): https://github.com/psg-mit/nightjarpy


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


## Workflow styles (hardness vs flexibility)

This section summarizes the tradeoffs in terms of "hard control" vs "flexibility".

### 1 Nightjar style (hard control, embedded Natural blocks)

You write strict flow in Python, and embed Natural blocks where semantics are needed.

Pros:
- Hard guarantees: exact loops, strict conditionals, deterministic boundaries.
- Tools: debuggers, tests, linters, and normal software engineering practices apply.
- The LLM is "physically constrained" to operate on interpreter-visible objects (locals, memory, tool context).

Cons:
- Knowledge often ends up encoded in code-adjacent artifacts, which can be less maintainable by non-engineers.

Pseudo-code example:

```py
import nightjarpy as nj

@nj.fn
def calculate_average(numbers):
    """natural
    Consider the values of <numbers> and compute the semantic average as <:result>
    """
    return result

result = calculate_average([1, "2", "three", "cuatro", "五"])
print(result)  # 3.0
```

### 2 Skills-style / reverse Nightjar (flexible workflow, code snippets as needed)

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

### 3 Hybrid nesting (Natural -> Python -> Natural -> ...)

Allow nested alternation between natural language and code.

Pros:
- Potentially expressive enough to cover many patterns.

Cons:
- Inherits the reverse-Nightjar synchronization problem, and then amplifies it by adding nesting complexity.

Pseudo-code example:

````md
Compute the "semantic average" of the target list using the following function.
However, the target list contains mixed numeric representations, so convert the elements appropriately before calling the function and passing them as the argument.

```py
def calculate_average(numbers):
    """natural
    Consider the values of <numbers> and compute the length as <:length>
    """
    return sum(numbers) / length
```

Target list: `[1, "2", "three", "cuatro", "五"]`
````


## Mapping LLM internal state into the interpreter

LLM reasoning is usually a black box. Nighthawk explores a different model:

- The LLM should write any durable intermediate state into **Python-visible state**:
  - local variables (committed at Natural block boundaries), and/or
  - a structured "memory" object (Pydantic model).

Why this matters:
- You can inspect state directly in Python.
- You can validate/coerce updates (types and schemas).
- In the long run, this could enable tighter debugging workflows where the agent's state is inspectable and testable.


## Repository smoke check (current stub mode)

This is not a stable user install flow. It is a minimal repo sanity check.

Prereqs:
- git
- uv
- Python 3.14+

Run:

```bash
git clone <this-repo>
cd nighthawk-python
uv sync
uv run python -m pytest -q tests/test_readme_example.py
```

If the test passes, the minimal API shape (decorator + Natural docstring block) is working in stub mode.


## Minimal example (stub mode)

```py
import nighthawk as nh

@nh.fn
def calculate_average(numbers: list[int]):
    """natural
    <numbers>
    <:result>
    {{"natural_final": {{"effect": null, "error": null}}, "outputs": {{"result": {sum(numbers) / len(numbers)}}}}}
    """
    return result  # type: ignore[name-defined]
```

Notes:
- A Natural block is a docstring or a standalone string literal whose first non-empty line is exactly `natural`.
- `<name>` is an input binding and `<:name>` is an output binding.
- In stub mode, you write a JSON envelope with `natural_final` and `outputs` inside the block to drive updates.
- Because the block is preprocessed as a template string, literal `{` / `}` are written as `{{` / `}}`.


## Safety model (short)

- Natural blocks and any included markdown are assumed to be trusted, repository-managed assets.
- Do not feed untrusted user-generated content into template preprocessing or include helpers.


## References

- Nightjar (upstream concept): https://github.com/psg-mit/nightjarpy
- Agent Skills (external article): https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
