# Nighthawk development guide for coding agents

> **For coding agents.** Give this document to your coding agent (e.g., via `@docs/for-coding-agents.md` in CLAUDE.md or AGENTS.md) to enable Nighthawk-aware development.

You are expected to behave as a senior software engineer with deep Nighthawk experience. This document contains the knowledge you need to write, review, and debug Nighthawk code. Apply these principles without prompting.

## 1. Core mental model

Nighthawk separates **hard control** (Python) from **soft reasoning** (an LLM or coding agent). Python owns all deterministic logic; the LLM or coding agent handles semantic interpretation inside small embedded **Natural blocks**.

Key invariants:

- The Python interpreter is the primary external memory. All intermediate state lives as Python locals or structured objects, not hidden chat history.
- Each Natural block executes independently. There is no implicit message history between blocks. Cross-block context must be explicit.
- Write bindings (`<:name>`) are the only way the LLM commits values back into Python locals.

**Use Natural** when the task requires LLM judgment -- classification, generation, interpretation, or selection that depends on context or world knowledge.

**Use Python** for everything deterministic -- computation, control flow, I/O, validation, state management, and data plumbing between Natural blocks. If the correct output can be computed without an LLM, use Python.

## 2. Writing Natural blocks

### Anatomy

A Natural block is a docstring or standalone string literal beginning with `natural\n`. Nighthawk assembles a prompt with three sections: `<<<NH:PROGRAM>>>` (block text), `<<<NH:LOCALS>>>` (step locals as `name: type = value`), `<<<NH:GLOBALS>>>` (module-level names referenced via `<name>`).

### Bindings

- `<name>` -- read binding. Visible to the LLM; cannot be rebound. Mutable objects can be mutated in-place.
- `<:name>` -- write binding. LLM sets a new value, committed into Python locals after the block.

**Rule:** always annotate write bindings with types. This enables validation and coercion at commit time.

### One block, one task

Each Natural block performs exactly one task: one set of input bindings, one set of output bindings, one outcome. If a block makes two independent decisions, split it into two blocks connected by Python.

### Interpolation

- Docstring Natural blocks are always literal (no interpolation).
- Inline f-string Natural blocks (`f"""natural\n..."""`) evaluate Python expressions before the LLM sees the prompt.
- Use f-strings for static config and pre-formatted context; use `<name>` bindings for mutable state and objects the LLM inspects or modifies.

### Async

Async works identically; Nighthawk auto-awaits binding function returns. Expressions evaluated by tools may use `await`.

## 3. Designing binding functions

Binding functions (local or module-level callables) are the preferred way to expose functions to the LLM. The LLM discovers them from the LOCALS/GLOBALS prompt sections, rendered as their signature with the first docstring line as `# intent:`.

### Keep locals minimal

**Rule:** module-level names stable across invocations belong in GLOBALS via `<name>` read bindings. Reserve function parameters for per-call data.

```py
# Wrong -- fetch_data loses its signature in LOCALS:
async def summarize(query: str, fetch_data: object) -> str: ...

# Correct -- fetch_data keeps its full signature in GLOBALS:
@nh.natural_function
async def summarize(query: str) -> str:
    result = ""
    """natural
    Use <fetch_data> to get data for <query> and set <:result>.
    """
    return result
```

### Minimize LLM cognitive load

**Rule:** each parameter in a binding function is a decision point. Compose complex operations in Python and expose simple interfaces.

```py
# Wrong -- too many parameters
def find_items(category: str, min_score: float, max_score: float,
               tags: list[str], created_after: str, sort_by: str) -> list[dict]: ...

# Correct -- simple interface, complexity in Python
def find_top_items(category: str) -> list[dict]:
    """Return the highest-scored recent items in a category."""
    return query_items(category=category, min_score=0.8,
                       tags=get_relevant_tags(category),
                       created_after=recent_cutoff(), sort_by="score_desc")
```

### Docstrings matter

Write short docstrings explaining intent and boundaries. The first line appears as `# intent:` in the prompt. Clear function names and accurate type annotations complete discoverability.

## 4. Control flow and error handling

### Outcomes

| Outcome | Effect | Available when |
|---|---|---|
| `pass` | Continue to next statement | Always |
| `return` | Return from surrounding function | Always |
| `break` | Break from enclosing loop | Inside a loop |
| `continue` | Continue to next iteration | Inside a loop |
| `raise` | Raise an exception | Always |

### Deny frontmatter

**Rule:** restrict outcomes with YAML frontmatter. **Template:**

```py
"""natural
---
deny: [raise, return]
---
Read <text> and set <:result> to a summary.
"""
```

### Error handling

**Rule:** the LLM signals errors via `raise`. Catch with `except nh.ExecutionError`. Custom exception types in step locals/globals are available as raise targets. All Nighthawk exceptions inherit from `nh.NighthawkError`.

```py
try:
    validate(data)
except nh.ExecutionError as e:
    print(f"Validation failed: {e}")
```

## 5. Cross-block composition

### The carry pattern

Pass a mutable object as a read binding (`<carry>`, not `<:carry>`) and instruct the LLM to mutate it in-place:

```py
@nh.natural_function
def step_1(carry: list[str]) -> int:
    result = 0
    """natural
    Set <:result> to 10.
    Append a one-line summary of what you did to <carry>.
    """
    return result

carry: list[str] = []
r1 = step_1(carry)   # carry now has 1 entry
r2 = step_2(carry)   # carry now has 2 entries
```

**Critical:** use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent rebinding, preserving the caller's reference. Branch by copying (`carry_a = carry.copy()`); when carry grows too large, inject context via f-string instead.

## 6. Execution configuration

Natural functions must be called inside `with nh.run(step_executor):`.

```py
step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.4-mini"),
)
with nh.run(step_executor):
    result = my_natural_function(data)
```

Use `nh.scope()` to override model, prompts, or context limits within an existing run:

```py
with nh.run(step_executor):
    # Override model for a specific section
    with nh.scope(
        step_executor_configuration_patch=nh.StepExecutorConfigurationPatch(
            model="openai-responses:gpt-5.4-mini",
        ),
    ):
        expensive_analysis(data)

    # Append a system prompt suffix
    with nh.scope(system_prompt_suffix_fragment="Always respond in formal English."):
        formal_summary(text)
```

For the full parameter list, see [Tutorial](https://kurusugawa-computer.github.io/nighthawk-python/tutorial/#scoped-overrides-with-nhscope).

**Rule:** when bindings are missing or truncated (`<snipped>`), adjust `StepContextLimits` in the configuration. See [Design](https://kurusugawa-computer.github.io/nighthawk-python/design/) for field details.

## 7. Testing

### Testing strategy

| Layer | What it tests | What it cannot test |
|---|---|---|
| **Mock tests** (`nighthawk.testing`) | Python logic: control flow, error handling, composition, binding wiring | Natural block effectiveness, prompt quality, LLM behavior |
| **Integration tests** (real LLM) | Whether the Natural block text produces correct judgments | Deterministic reproducibility (LLMs are non-deterministic) |

**Guideline:** mock tests lock down the deterministic Python shell; integration tests validate prompt quality. Do not rely on mock tests as the primary quality gate -- they pass even when the Natural block text is wrong.

### Mock tests

`ScriptedExecutor` returns scripted responses and records every call in `executor.calls`.

```py
from nighthawk.testing import ScriptedExecutor, pass_response, raise_response

executor = ScriptedExecutor(responses=[
    pass_response(result="Three key points: ..."),
])
with nh.run(executor):
    output = summarize("long document")

assert output == "Three key points: ..."
```

**Rule:** for multi-step functions, use `default_response` to avoid enumerating every response: `ScriptedExecutor(default_response=pass_response(result=""))`.

**Rule:** use `CallbackExecutor(handler)` when response logic depends on input. It also records calls in `executor.calls`.

**Rule:** verify binding wiring via `executor.calls[0]`: check `call.step_globals`, `call.step_locals`, `call.binding_names`.

#### Outcome factories

| Factory | Outcome | Use case |
|---|---|---|
| `pass_response(**bindings)` | pass | Normal completion with binding values |
| `raise_response(message, *, error_type=None)` | raise | Test error handling paths |
| `return_response(reference_path, **bindings)` | return | Early return from Natural function |
| `break_response()` | break | Exit enclosing loop |
| `continue_response()` | continue | Skip to next iteration |

### Integration tests

**Rule:** gate behind `NIGHTHAWK_RUN_INTEGRATION_TESTS=1`. Assert on type, value range, and semantic consistency -- not exact string matches.

```py
import os, pytest
if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
    pytest.skip("Integration tests disabled", allow_module_level=True)
```

## 8. Common mistakes to avoid

| Mistake | Why it breaks | Fix |
|---|---|---|
| Pass a callable as a parameter with generic type (`object`, `Any`) | Signature erased in LOCALS; LLM cannot discover arguments | Reference via `<name>` read binding so it appears in GLOBALS with full signature |
| Use `<:carry>` (write binding) for mutable context | Rebinding breaks the caller's reference | Use `<carry>` (read binding); mutate in-place |
| Put two independent tasks in one block | Non-deterministic, hard to test, unclear contract | Split into two blocks connected by Python |
| Use Natural for deterministic computation | Wastes latency/cost, adds non-determinism | Use Python |

For the full list, see [Practices](https://kurusugawa-computer.github.io/nighthawk-python/practices/#5-common-mistakes).

## References

- [Tutorial](https://kurusugawa-computer.github.io/nighthawk-python/tutorial/) -- learn Nighthawk from first principles (human-oriented).
- [Practices](https://kurusugawa-computer.github.io/nighthawk-python/practices/) -- practical patterns and guidelines.
- [Providers](https://kurusugawa-computer.github.io/nighthawk-python/providers/) -- LLM providers and configuration.
- [Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/) -- backend configuration for Claude Code and Codex.
- [Design](https://kurusugawa-computer.github.io/nighthawk-python/design/) -- canonical specification.
- [API Reference](https://kurusugawa-computer.github.io/nighthawk-python/api/) -- auto-generated API documentation.
