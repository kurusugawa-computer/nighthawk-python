# Nighthawk development guide for coding agents

> **For coding agents.** Give this document to your coding agent (e.g., via `@docs/for-coding-agents.md` in CLAUDE.md or AGENTS.md) to enable Nighthawk-aware development.

You are expected to behave as a senior software engineer with deep Nighthawk experience. This document contains the knowledge you need to write, review, and debug Nighthawk code. Apply these principles without prompting.

## 1. Core mental model

Nighthawk separates **hard control** (Python) from **soft reasoning** (LLM). Python owns all deterministic logic: loops, conditionals, data plumbing, I/O. The LLM handles semantic interpretation inside small embedded **Natural blocks**.

Key invariants:

- The Python interpreter is the primary external memory. All intermediate state lives as Python locals or structured objects, not hidden chat history.
- Each Natural block executes independently. There is no implicit message history between blocks. Cross-block context must be explicit.
- Write bindings (`<:name>`) are the only way the LLM commits values back into Python locals. The LLM is physically constrained to operate on interpreter-visible objects.

## 2. When to use Natural blocks

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

## 3. Writing Natural blocks

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

## 4. Designing binding functions

Binding functions (local or module-level callables) are the preferred way to expose functions to the LLM. The LLM discovers them from the LOCALS/GLOBALS sections of the prompt, rendered as their signature with the first docstring line as `# intent:`.

### Prefer binding functions over `@nh.tool`

`@nh.tool` is reserved strictly for cases requiring `RunContext[StepContext]` access. Binding functions incur no per-definition token overhead beyond a signature line. Always use binding functions unless `RunContext` access is needed.

### Keep locals minimal

Module-level names that are stable across invocations (constants, classes, utility functions) should stay in GLOBALS via `<name>` read bindings. Reserve function parameters for data that genuinely varies per call.

Wrong -- `fetch_data` loses its signature in LOCALS:

```python
@nh.natural_function
async def summarize(query: str, fetch_data: object) -> str:
    result = ""
    """natural
    Use <fetch_data> to get data for <query> and set <:result>.
    """
    return result
```

Correct -- `fetch_data` keeps its full signature in GLOBALS:

```python
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

```python
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

## 5. Control flow and error handling

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

```python
"""natural
---
deny: [raise, return]
---
Read <text> and set <:result> to a summary.
"""
```

### Error handling pattern

The LLM signals errors via the `raise` outcome. Catch with standard Python:

```python
try:
    validate(data)
except nh.ExecutionError as e:
    print(f"Validation failed: {e}")
```

Custom exception types referenced in step locals or globals are available as raise targets.

### Exception hierarchy

All exceptions inherit from `NighthawkError`: `ExecutionError`, `NaturalParseError`, `ToolEvaluationError`, `ToolValidationError`, `ToolRegistrationError`.

## 6. Cross-block composition

### The carry pattern

Pass a mutable object as a read binding (`<carry>`, not `<:carry>`) and instruct the LLM to mutate it in-place:

```python
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

### Branching

Copy the carry to create independent branches:

```python
carry_a = carry.copy()
carry_b = carry.copy()
result_a = branch_add(carry_a)
result_b = branch_multiply(carry_b)
```

### f-string injection as alternative

When the carry's locals summary footprint is too large, inject pre-formatted context via f-string:

```python
f"""natural
Prior context: {context_text}
Set <:result> based on the context.
"""
```

## 7. Execution configuration

### Run context

Natural functions must be called inside `with nh.run(step_executor):`. For backend-specific settings, see [Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/).

```python
step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini"),
)
with nh.run(step_executor):
    result = my_natural_function(data)
```

### Scoped overrides

Use `nh.scope()` to override execution settings within an existing run. Each scope generates a new `scope_id` while keeping the current `run_id`.

Parameters:

- `step_executor_configuration`: replace the entire configuration.
- `step_executor_configuration_patch`: partially override specific fields.
- `step_executor`: replace the step executor entirely.
- `system_prompt_suffix_fragment`: append text to the system prompt for the scope.
- `user_prompt_suffix_fragment`: append text to the user prompt for the scope.

```python
with nh.scope(
    step_executor_configuration_patch=nh.StepExecutorConfigurationPatch(
        model="openai-responses:gpt-5-mini",
    ),
):
    expensive_analysis(data)
```

### Context limits

LOCALS and GLOBALS sections are bounded by `StepContextLimits`. Configure via `StepExecutorConfiguration`:

```python
configuration = nh.StepExecutorConfiguration(
    model="openai-responses:gpt-5-mini",
    context_limits=nh.StepContextLimits(
        locals_max_tokens=4096,
        locals_max_items=50,
    ),
)
```

## 8. Testing

Use Pydantic AI's `TestModel` for deterministic unit tests without API calls:

```python
from nighthawk.runtime.step_executor import AgentStepExecutor
from nighthawk.configuration import StepExecutorConfiguration
from pydantic_ai.models.test import TestModel

configuration = StepExecutorConfiguration(model="openai-responses:gpt-5-nano")
executor = AgentStepExecutor(configuration=configuration, agent=TestModel())

with nh.run(executor):
    # Natural functions use TestModel -- deterministic, no API calls
    ...
```

## 9. Type boundary placement

For deterministic functions (no Natural blocks), the type boundary is at the function entry point -- use typed inputs.

For judgment-heavy functions (containing Natural blocks), the type boundary moves inside the function. Accept flexible inputs at the entry point and let the Natural block interpret them into typed intermediates via write bindings:

```python
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

## 10. Common mistakes to avoid

| Mistake | Why it breaks | Fix |
|---|---|---|
| Pass a callable as a parameter with generic type (`object`, `Any`) | Signature erased in LOCALS; LLM cannot discover arguments | Reference via `<name>` read binding so it appears in GLOBALS with full signature |
| Use `<:carry>` (write binding) for mutable context | Rebinding breaks the caller's reference | Use `<carry>` (read binding); mutate in-place |
| Put two independent judgments in one block | Non-deterministic, hard to test, unclear contract | Split into two blocks connected by Python |
| Use Natural for deterministic computation | Wastes latency/cost, adds non-determinism | Use Python |
| Use `@nh.tool` when a binding function suffices | Unnecessary per-definition token overhead | Use binding functions; reserve `@nh.tool` for `RunContext[StepContext]` access |
| Forget type annotations on write bindings | No validation or coercion at commit time | Always annotate `<:name>` bindings |
| Duplicate module-level constants as function parameters | Moves stable values from GLOBALS to LOCALS, wastes tokens | Reference via `<name>` read binding |

## 11. Quick reference

### Imports and setup

```python
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini"),
)
with nh.run(step_executor):
    ...
```

### Natural function template

```python
@nh.natural_function
def my_function(input_data: str) -> str:
    result: str = ""
    """natural
    Read <input_data> and set <:result> to the processed output.
    """
    return result
```

### Async natural function

Async natural functions work identically to sync ones, with two additions: expressions evaluated by tools may use `await`, and return values that are awaitable are automatically awaited before validation.

```python
@nh.natural_function
async def my_async_function(text: str) -> str:
    result: str = ""
    """natural
    Summarize <text> and set <:result>.
    """
    return result
```

### Binding function pattern

```python
def helper(query: str) -> list[str]:
    """Fetch items matching the query."""
    ...

@nh.natural_function
def process(query: str) -> str:
    result = ""
    """natural
    Call <helper> with <query> and set <:result> to a summary of the results.
    """
    return result
```

## References

- [Tutorial](https://kurusugawa-computer.github.io/nighthawk-python/tutorial/) -- learn Nighthawk from first principles (human-oriented).
- [Providers](https://kurusugawa-computer.github.io/nighthawk-python/providers/) -- LLM providers and configuration.
- [Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/) -- backend configuration for Claude Code and Codex.
- [Design](https://kurusugawa-computer.github.io/nighthawk-python/design/) -- canonical specification.
- [API Reference](https://kurusugawa-computer.github.io/nighthawk-python/api/) -- auto-generated API documentation.
