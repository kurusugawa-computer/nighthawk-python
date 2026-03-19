# Nighthawk development guide for coding agents

> **For coding agents.** Give this document to your coding agent (e.g., via `@docs/for-coding-agents.md` in CLAUDE.md or AGENTS.md) to enable Nighthawk-aware development.

You are expected to behave as a senior software engineer with deep Nighthawk experience. This document contains the knowledge you need to write, review, and debug Nighthawk code. Apply these principles without prompting.

## 1. Core mental model

Nighthawk separates **hard control** (Python) from **soft reasoning** (LLM). Python owns all deterministic logic: loops, conditionals, data plumbing, I/O. The LLM handles semantic interpretation inside small embedded **Natural blocks**.

Key invariants:

- The Python interpreter is the primary external memory. All intermediate state lives as Python locals or structured objects, not hidden chat history.
- Each Natural block executes independently. There is no implicit message history between blocks. Cross-block context must be explicit.
- Write bindings (`<:name>`) are the only way the LLM commits values back into Python locals. The LLM is physically constrained to operate on interpreter-visible objects.

**Use Natural when the task requires LLM judgment** -- decisions that depend on interpretation, world knowledge, or subjective evaluation:

- Classification and routing (e.g., categorize a support ticket).
- Text generation (summarize, draft, translate, reformulate).
- Interpretation of ambiguous or unstructured input.
- Selection among options based on context.

**Use Python for everything deterministic** -- operations whose result is fully determined by the input:

- Computation, string manipulation, data transformation.
- Control flow (loops, conditionals, sequencing).
- I/O, side effects, validation, error recovery.
- State management and data flow between Natural blocks.

**Decision rule:** if the correct output can be computed without an LLM, use Python. Natural blocks add latency, cost, and non-determinism.

## 2. Writing Natural blocks

### Anatomy

A Natural block is a docstring or standalone string literal beginning with `natural\n`. Nighthawk assembles a prompt with three sections:

- `<<<NH:PROGRAM>>>` -- the Natural block text.
- `<<<NH:LOCALS>>>` -- step locals rendered as `name: type = value` (alphabetical).
- `<<<NH:GLOBALS>>>` -- module-level names referenced via `<name>` that are not in step locals.

### Bindings

- `<name>` -- read binding. Value visible to the LLM. Cannot be rebound. Mutable objects can be mutated in-place.
- `<:name>` -- write binding. The LLM may set a new value, committed into Python locals after the block.

Type annotations on write bindings enable validation and coercion at commit time. Always annotate write bindings.

### One block, one judgment

Each Natural block should make exactly one independent judgment. If a block makes two independent decisions, split it into two blocks connected by Python. This makes each block testable, debuggable, and deterministic in its contract.

### Interpolation

- Docstring Natural blocks are always literal (no interpolation).
- Inline f-string Natural blocks (`f"""natural\n..."""`) evaluate Python expressions at f-string evaluation time, before the LLM sees the prompt.
- Use f-string injection for static config, pre-formatted context, computed values.
- Use `<name>` bindings for mutable state and objects the LLM needs to inspect or modify.

### Async

Async natural functions work identically to sync ones, with two additions: expressions evaluated by tools may use `await`, and return values that are awaitable are automatically awaited before validation.

## 3. Designing binding functions

Binding functions (local or module-level callables) are the preferred way to expose functions to the LLM. The LLM discovers them from the LOCALS/GLOBALS sections of the prompt, rendered as their signature with the first docstring line as `# intent:`.

### Keep locals minimal

Module-level names that are stable across invocations (constants, classes, utility functions) should stay in GLOBALS via `<name>` read bindings. Reserve function parameters for data that genuinely varies per call.

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

Each parameter in a binding function signature is a decision point the LLM must evaluate. Compose complex operations in Python and expose simple binding functions:

```py
# Wrong -- too many parameters
def find_items(category: str, min_score: float, max_score: float,
               tags: list[str], created_after: str, sort_by: str) -> list[dict]:
    ...

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

Each Natural block returns exactly one outcome:

| Outcome | Effect | Available when |
|---|---|---|
| `pass` | Continue to next statement | Always |
| `return` | Return from surrounding function | Always |
| `break` | Break from enclosing loop | Inside a loop |
| `continue` | Continue to next iteration | Inside a loop |
| `raise` | Raise an exception | Always |

### Deny frontmatter

Restrict allowed outcomes with YAML frontmatter:

```py
"""natural
---
deny: [raise, return]
---
Read <text> and set <:result> to a summary.
"""
```

### Error handling pattern

The LLM signals errors via the `raise` outcome. Catch with standard Python:

```py
try:
    validate(data)
except nh.ExecutionError as e:
    print(f"Validation failed: {e}")
```

Custom exception types referenced in step locals or globals are available as raise targets. Catch `nh.ExecutionError` for Natural block failures; all Nighthawk exceptions inherit from `nh.NighthawkError`.

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

Critical: use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent rebinding, preserving the caller's reference.

- Branch by copying the carry (`carry_a = carry.copy()`). Each copy continues independently.
- When the carry's token footprint is too large, inject context via f-string instead ([Section 2](#interpolation)).

## 6. Execution configuration

### Run context

Natural functions must be called inside `with nh.run(step_executor):`. For backend-specific settings, see [Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/).

```py
step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini"),
)
with nh.run(step_executor):
    result = my_natural_function(data)
```

Use `nh.scope()` to override model, prompts, or context limits within an existing run. For details, see [Tutorial](https://kurusugawa-computer.github.io/nighthawk-python/tutorial/).

LOCALS and GLOBALS sections are bounded by `StepContextLimits`. When bindings are missing or truncated (`<snipped>`), adjust the limits:

```py
configuration = nh.StepExecutorConfiguration(
    model="openai-responses:gpt-5-mini",
    context_limits=nh.StepContextLimits(
        locals_max_tokens=4096,
        locals_max_items=50,
    ),
)
```

## 7. Testing

### Testing strategy

Mock tests exercise the Python logic around Natural blocks -- control flow, error handling, composition, binding wiring. They do **not** exercise the Natural blocks themselves. Since Natural blocks are the core of a Nighthawk application, mock tests alone are insufficient.

| Layer | What it tests | What it cannot test |
|---|---|---|
| **Mock tests** (`nighthawk.testing`) | Python logic: control flow, error handling, composition, binding wiring | Natural block effectiveness, prompt quality, LLM behavior |
| **Integration tests** (real LLM) | Whether the Natural block text actually produces correct judgments | Deterministic reproducibility (LLMs are non-deterministic) |

**Guideline:** use mock tests to lock down the deterministic Python shell, then use integration tests to validate that each Natural block's prompt elicits the intended judgment. Do not rely on mock tests as the primary quality gate -- a mock test passes even when the Natural block text is completely wrong.

### Mock tests

`ScriptedExecutor` returns scripted responses and records every call. Use it for Python logic that surrounds Natural blocks.

```py
from nighthawk.testing import ScriptedExecutor, pass_response, raise_response

executor = ScriptedExecutor(responses=[
    pass_response(result="Three key points: ..."),
])
with nh.run(executor):
    output = summarize("long document")

assert output == "Three key points: ..."

# Inspect what was passed to the executor
call = executor.calls[0]
assert "result" in call.binding_names        # write binding registered
assert call.step_locals["text"] == "long document"  # locals visible
```

For multi-step functions, pass `default_response` to avoid enumerating every response:

```py
executor = ScriptedExecutor(default_response=pass_response(result=""))
```

#### Outcome factories

| Factory | Outcome | Use case |
|---|---|---|
| `pass_response(**bindings)` | pass | Normal completion with binding values |
| `raise_response(message, *, error_type=None)` | raise | Test error handling paths |
| `return_response(reference_path, **bindings)` | return | Early return from Natural function |
| `break_response()` | break | Exit enclosing loop |
| `continue_response()` | continue | Skip to next iteration |

```py
# Error handling:
executor = ScriptedExecutor(responses=[
    raise_response("invalid input", error_type="ValueError"),
])

# Early return:
executor = ScriptedExecutor(responses=[
    return_response("result", result="early exit"),
])
```

#### Callback executor

`CallbackExecutor` delegates to a callback when response logic depends on input. Like `ScriptedExecutor`, it records calls in `executor.calls`:

```py
from nighthawk.testing import CallbackExecutor, StepCall, StepResponse

def handler(call: StepCall) -> StepResponse:
    text = call.step_locals.get("text", "")
    if isinstance(text, str) and "urgent" in text:
        return pass_response(priority="high")
    return pass_response(priority="normal")

executor = CallbackExecutor(handler)
with nh.run(executor):
    assert triage("urgent outage") == "high"
```

#### Binding wiring verification

Use recorded calls to verify that the right data is visible to the LLM:

```py
executor = ScriptedExecutor(responses=[pass_response(result="")])
with nh.run(executor):
    process(query="test")

call = executor.calls[0]
assert "helper" in call.step_globals   # binding function visible in GLOBALS
assert "query" in call.step_locals     # parameter visible in LOCALS
assert "result" in call.binding_names  # write binding registered
```

### Integration tests

Integration tests call a real LLM and validate the judgment. This is where Natural block quality is actually tested.

```py
step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini"),
)
with nh.run(step_executor):
    verdict = judge_review("The code has no error handling and uses eval().")

assert not verdict.approved
assert verdict.risk_level in ("high", "critical")
```

For structured outputs, assert on type, value range, and semantic consistency rather than exact string matches. LLMs are non-deterministic; brittle equality checks cause flaky tests.

Gate integration tests behind an environment variable so they do not run in every CI job:

```py
import os
import pytest

if os.getenv("NIGHTHAWK_RUN_INTEGRATION_TESTS") != "1":
    pytest.skip("Integration tests disabled", allow_module_level=True)
```

## 8. Type boundary placement

For deterministic functions (no Natural blocks), the type boundary is at the function entry point -- use typed inputs.

For judgment-heavy functions (containing Natural blocks), the type boundary moves inside the function. Accept flexible inputs at the entry point and let the Natural block interpret them into typed intermediates via write bindings:

```py
from pydantic import BaseModel

class ReviewVerdict(BaseModel):
    approved: bool
    reason: str
    risk_level: str

@nh.natural_function
def judge_review(review_data: str | nh.JsonableValue) -> ReviewVerdict:
    verdict: ReviewVerdict
    """natural
    Analyze <review_data> and produce a structured <:verdict>.
    """
    return verdict
```

## 9. Common mistakes to avoid

| Mistake | Why it breaks | Fix |
|---|---|---|
| Pass a callable as a parameter with generic type (`object`, `Any`) | Signature erased in LOCALS; LLM cannot discover arguments | Reference via `<name>` read binding so it appears in GLOBALS with full signature |
| Use `<:carry>` (write binding) for mutable context | Rebinding breaks the caller's reference | Use `<carry>` (read binding); mutate in-place |
| Put two independent judgments in one block | Non-deterministic, hard to test, unclear contract | Split into two blocks connected by Python |
| Use Natural for deterministic computation | Wastes latency/cost, adds non-determinism | Use Python |
| Forget type annotations on write bindings | No validation or coercion at commit time | Always annotate `<:name>` bindings |
| Duplicate module-level constants as function parameters | Moves stable values from GLOBALS to LOCALS, wastes tokens | Reference via `<name>` read binding |

## References

- [Tutorial](https://kurusugawa-computer.github.io/nighthawk-python/tutorial/) -- learn Nighthawk from first principles (human-oriented).
- [Providers](https://kurusugawa-computer.github.io/nighthawk-python/providers/) -- LLM providers and configuration.
- [Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/) -- backend configuration for Claude Code and Codex.
- [Design](https://kurusugawa-computer.github.io/nighthawk-python/design/) -- canonical specification.
- [API Reference](https://kurusugawa-computer.github.io/nighthawk-python/api/) -- auto-generated API documentation.
