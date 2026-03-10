# Nighthawk Tutorial
<!-- prompt-example markers (e.g. prompt-example:basic-binding) are test anchors used by tests/docs/test_prompt_examples.py to verify that documented prompt examples match actual rendering output. -->

This tutorial builds your understanding of Nighthawk from first principles. It assumes you have completed the [Quickstart](quickstart.md) and can run a basic Natural block.

## 1. Anatomy of a Natural Block

Every Natural block execution follows the same pattern: Nighthawk assembles a prompt, sends it to the LLM, and the LLM responds with tool calls and a final outcome. Understanding the prompt structure is the key to writing effective Natural blocks.

### What the LLM receives

When you write:

```py
@nh.natural_function
def classify_priority(text: str) -> str:
    priority: str = "normal"
    """natural
    Read <text> and update <:priority> with one of: low, normal, high.
    """
    return priority
```

And call `classify_priority("Server is on fire!")`, Nighthawk assembles this user prompt:

<!-- prompt-example:basic-binding -->
```py
<<<NH:PROGRAM>>>
Read <text> and update <:priority> with one of: low, normal, high.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
priority: str = "normal"
text: str = "Server is on fire!"
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:basic-binding -->

Three sections:

- **`<<<NH:PROGRAM>>>`** — your Natural block text (after sentinel removal and `textwrap.dedent`).
- **`<<<NH:LOCALS>>>`** — step locals, rendered alphabetically as `name: type = value`.
- **`<<<NH:GLOBALS>>>`** — module-level names referenced via `<name>` that are not in step locals.

### One block, one execution

Each Natural block executes independently. There is no implicit message history between blocks. The LLM sees only the prompt assembled for that specific block. Cross-block context must be explicit (see [Section 5](#5-cross-block-composition)).

## 2. Providing Data to a Block

Two mechanisms supply data to a Natural block: **bindings** and **f-string injection**.

### Read bindings (`<name>`)

A read binding makes a Python value visible in the LOCALS section. The name cannot be rebound by the LLM.

```py
@nh.natural_function
def step(carry: list[str]) -> int:
    result = 0
    """natural
    Read <carry> for prior context.
    Set <:result> to 10.
    Append a one-line summary of what you did to <carry>.
    """
    return result
```

`carry` is a read binding — the LLM can read it and mutate it in-place (e.g., `list.append()`), but it cannot rebind the name.

### Write bindings (`<:name>`)

A write binding allows the LLM to set a new value via `nh_assign`. The value is committed back into Python locals after the block.

Pre-declared (with type annotation):

```py
@nh.natural_function
def classify_priority(text: str) -> str:
    priority: str = "normal"
    """natural
    Read <text> and update <:priority> with one of: low, normal, high.
    """
    return priority
```

Not pre-declared:

```py
@nh.natural_function
def classify_priority(text: str):
    """natural
    Read <text> and set <:priority>.
    """
    # `priority` is intentionally introduced by <:priority>.
    return priority
```

Type annotations on write bindings enable validation and coercion at commit time.

### f-string injection

Inline f-string blocks embed Python expressions directly into the Natural program text. The expression is evaluated when Python evaluates the f-string — before the LLM sees the prompt.

```py
PROJECT_POLICY = ["safety-first", "concise-output", "cite-assumptions"]

@nh.natural_function
def choose_policy(post: str) -> str:
    selected_policy = ""
    f"""natural
    Read <post>.
    Available policies: {PROJECT_POLICY}
    Select the single best policy and set <:selected_policy>.
    """
    return selected_policy
```

Calling `choose_policy("Breaking: earthquake hits downtown")` produces:

<!-- prompt-example:fstring-injection -->
```py
<<<NH:PROGRAM>>>
Read <post>.
Available policies: ['safety-first', 'concise-output', 'cite-assumptions']
Select the single best policy and set <:selected_policy>.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
post: str = "Breaking: earthquake hits downtown"
selected_policy: str = ""
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:fstring-injection -->

Notice: `{PROJECT_POLICY}` was evaluated into literal text in the PROGRAM section, while `post` and `selected_policy` appear as bindings in LOCALS.

Member accesses and function results work too:

```py
from pydantic import BaseModel

class Config(BaseModel):
    max_length: int = 100
    style: str = "formal"

@nh.natural_function
def generate(config: Config, topic: str) -> str:
    output = ""
    f"""natural
    Write about <topic> in {config.style} style.
    Keep the output under {config.max_length} words.
    Set <:output>.
    """
    return output
```

### Choosing between bindings and injection

| | f-string injection | `<name>` binding |
|---|---|---|
| Evaluation time | When Python evaluates the f-string literal | At LLM prompt construction |
| Appears in | Natural program text directly | Locals summary |
| Token control | Full — you decide the exact text | Governed by `context_limits` |
| LLM can mutate | No (text is baked in) | Yes (via `nh_exec`) |
| Brace escaping | `{{` / `}}` to produce literal `{` / `}` | N/A |
| Best for | Static config, pre-formatted context, computed values | Mutable state, objects the LLM needs to inspect or modify |

## 3. Functions and Discoverability

The LLM discovers callable functions from the LOCALS and GLOBALS sections of the prompt. Callable values are rendered as their signature, with the first line of the docstring appended as `# intent:`.

### Local functions

```py
@nh.natural_function
def compute_score_with_local_function() -> int:
    def add_points(base: int, bonus: int) -> int:
        """Return a deterministic sum for score calculation."""
        return base + bonus

    result = 0
    """natural
    Compute <:result> by choosing the most suitable local helper based on its docstring.
    Use base=38 and bonus=4.
    """
    return result
```

The LLM sees:

<!-- prompt-example:local-function-signature -->
```py
<<<NH:PROGRAM>>>
Compute <:result> by choosing the most suitable local helper based on its docstring.
Use base=38 and bonus=4.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
add_points: (base: int, bonus: int) -> int  # intent: Return a deterministic sum for score calculation.
result: int = 0
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:local-function-signature -->

### Module-level functions

When a Natural block references a module-level name via `<name>`, it appears in the GLOBALS section:

```py
def python_average(numbers):
    return sum(numbers) / len(numbers)

@nh.natural_function
def calculate_average(numbers):
    """natural
    Map each element of <numbers> to the number it represents,
    then compute <:result> by calling <python_average> with the mapped list.
    """
    return result
```

Calling `calculate_average([1, "2", "three", "cuatro"])` produces:

<!-- prompt-example:global-function-reference -->
```py
<<<NH:PROGRAM>>>
Map each element of <numbers> to the number it represents,
then compute <:result> by calling <python_average> with the mapped list.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
numbers: list = [1,"2","three","cuatro"]
result: int = 0
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>
python_average: (numbers)
<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:global-function-reference -->

### Custom tools with `@nh.tool`

For boundary-crossing side effects, register tools explicitly:

```py
@nh.tool(name="add_points")
def add_points(run_context, *, base: int, bonus: int) -> int:
    """Return a deterministic sum for score calculation."""
    _ = run_context
    return base + bonus
```

The first parameter `run_context` is a Pydantic AI `RunContext[StepContext]` injected automatically by the framework. It is not exposed to the LLM as a tool argument. See [Pydantic AI Tools documentation](https://ai.pydantic.dev/tools/) for details.

Author for discoverability:

- Use clear function names.
- Keep type annotations accurate.
- Write short docstrings that explain intent and boundaries.

## 4. Control Flow and Error Handling

Natural blocks can drive Python control flow when the surrounding syntax allows it. The LLM returns a final outcome that determines what happens next.

### Outcomes

| Outcome | Effect | Available when |
|---------|--------|----------------|
| `pass` | Continue to next Python statement | Always |
| `return` | Return from the surrounding function | Always |
| `break` | Break from the enclosing loop | Inside a loop |
| `continue` | Continue to the next loop iteration | Inside a loop |
| `raise` | Raise an exception | Always |

```py
@nh.natural_function
def process_posts(posts: list[str]) -> list[str]:
    summaries: list[str] = []

    for post in posts:
        """natural
        Evaluate <post>.
        If this post means "stop now", break.
        If this post should be skipped, continue.
        Otherwise assign <:summary>.
        """
        summaries.append(summary)

    return summaries
```

```py
@nh.natural_function
def route_message(message: str) -> str:
    """natural
    Evaluate <message>.
    If immediate exit is required, set <:result> and return.
    If the message is invalid, raise with a clear reason.
    Otherwise set <:result> and pass.
    """
    return f"NEXT:{result}"  # reached when the block outcome is `pass`
```

### Deny frontmatter

A Natural block can start with YAML frontmatter to restrict which outcomes are allowed:

```py
@nh.natural_function
def must_produce_result(text: str) -> str:
    result = ""
    """natural
    ---
    deny: [raise, return]
    ---
    Read <text> and set <:result> to a summary.
    """
    return result
```

See [design.md Section 8.4](design.md#84-execution-contract-final-json) for the full frontmatter specification.

### Error handling

Natural blocks signal errors via the `raise` outcome. Catch them on the Python side:

```py
@nh.natural_function
def validate(data: str) -> str:
    result = ""
    """natural
    Validate <data>. If invalid, raise with a clear reason.
    Otherwise set <:result> to "valid".
    """
    return result

try:
    validate("corrupted-input")
except nh.ExecutionError as e:
    print(f"Validation failed: {e}")
```

### Custom exception types

When a Natural block references exception types visible in step locals or globals, those types become available as `raise_error_type` options:

```py
class InputError(Exception):
    pass

@nh.natural_function
def strict_validate(data: str) -> str:
    result = ""
    """natural
    Validate <data>. If invalid, raise an <InputError>.
    Otherwise set <:result> to "valid".
    """
    return result

try:
    strict_validate("bad")
except InputError as e:
    print(f"Input error: {e}")
```

## 5. Cross-block Composition

Since each Natural block executes independently ([Section 1](#1-anatomy-of-a-natural-block)), cross-block context must be explicit.

### The carry pattern

Pass a mutable object as a read binding and let the LLM mutate it in-place:

```py
@nh.natural_function
def step_1(carry: list[str]) -> int:
    result = 0
    """natural
    Set <:result> to 10.
    Append a one-line summary of what you did to <carry>.
    """
    return result

@nh.natural_function
def step_2(carry: list[str]) -> int:
    result = 0
    """natural
    Read <carry> for prior context.
    The carry says the previous result was 10.
    Set <:result> to 20 (previous result plus 10).
    Append a one-line summary of what you did to <carry>.
    """
    return result

carry: list[str] = []
r1 = step_1(carry)   # carry now has 1 entry
r2 = step_2(carry)   # carry now has 2 entries
```

Any mutable object works — `list`, `dict`, Pydantic models, custom classes.

When `step_2(carry)` executes, the LLM sees the carry's current contents in LOCALS:

<!-- prompt-example:carry-pattern -->
```py
<<<NH:PROGRAM>>>
Read <carry> for prior context.
The carry says the previous result was 10.
Set <:result> to 20 (previous result plus 10).
Append a one-line summary of what you did to <carry>.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
carry: list = ["Set result to 10."]
result: int = 0
<<<NH:END_LOCALS>>>

<<<NH:GLOBALS>>>

<<<NH:END_GLOBALS>>>
```
<!-- /prompt-example:carry-pattern -->

### Branching

Branch a session by copying the carry. Each branch continues independently:

```py
carry: list[str] = []
seed_step(carry)

carry_a = carry.copy()
carry_b = carry.copy()

result_a = branch_add(carry_a)       # diverges from here
result_b = branch_multiply(carry_b)  # independent path
```

### f-string injection as alternative

When the carry's locals summary footprint is too large, or context is pre-formatted, inject it directly via f-string ([Section 2](#f-string-injection)):

```py
@nh.natural_function
def compute_with_context(context_text: str) -> int:
    result = 0
    f"""natural
    Prior context: {context_text}
    Based on the context, the previous result was 42.
    Set <:result> to 43 (previous result plus 1).
    """
    return result
```

### Design tips

- Use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent `nh_assign` from rebinding the name, which would break the caller's reference.
- Keep carry entries concise — they consume tokens in the locals summary on every subsequent step.

## 6. Execution Configuration

### Scoped overrides with `nh.scope()`

Use `nh.scope()` to override execution settings within an existing run. Each scope generates a new `scope_id` while keeping the current `run_id`.

```py
with nh.run(step_executor):

    # Override model for a specific section
    with nh.scope(
        step_executor_configuration_patch=nh.StepExecutorConfigurationPatch(
            model="openai-responses:gpt-5-mini",
        ),
    ) as scoped_executor:
        expensive_analysis(data)

    # Append a system prompt suffix for a section
    with nh.scope(
        system_prompt_suffix_fragment="Always respond in formal English.",
    ):
        formal_summary(text)
```

Parameters:

- `step_executor_configuration`: replace the entire configuration.
- `step_executor_configuration_patch`: partially override specific fields.
- `step_executor`: replace the step executor entirely.
- `system_prompt_suffix_fragment`: append text to the system prompt for the scope.
- `user_prompt_suffix_fragment`: append text to the user prompt for the scope.

The context manager yields the resolved `StepExecutor` for the scope.

### Async natural functions

Natural functions can be async:

```py
@nh.natural_function
async def summarize_async(text: str) -> str:
    result = ""
    """natural
    Summarize <text> in one sentence and set <:result>.
    """
    return result

summary = await summarize_async("A long document about climate change...")
```

Inside async natural functions:

- Expressions evaluated by tools may use `await` (e.g., `nh_eval("await some_async_func()")`).
- Return values that are awaitable are automatically awaited before validation.
- The carry pattern and all other patterns work identically in async context.

## 7. Writing Guidelines

### Responsibility split

- **Natural**: interpretation, synthesis, drafting.
- **Python**: ordering, loops, validation, reliability boundaries.

### Rules

1. Natural blocks must start with `natural\n` and no leading blank line.
2. Write one integrated instruction body; do not split into "bindings list" then "instructions".
3. Cross-block data flow must be explicit. Use Python locals, the carry pattern, or f-string injection.
4. Write bindings (`<:name>`) may be pre-declared or not. Type annotations help agent behavior and host-side validation/coercion.
5. Mutable context objects use `<name>` (read binding), not `<:name>` (write binding).
6. Side effects are bounded behind explicit, documented tools.
7. Full coverage requirements are enforced by Python loops.
8. Error behavior is explicit at the correct boundary.

## 8. Testing Natural Functions

For unit tests that do not call a real LLM, use Pydantic AI's `TestModel`. It returns a fixed structured output, making tests deterministic without API calls:

```py
import nighthawk as nh
from nighthawk.runtime.step_executor import AgentStepExecutor
from nighthawk.configuration import StepExecutorConfiguration
from pydantic_ai.models.test import TestModel

configuration = StepExecutorConfiguration(model="openai-responses:gpt-5-nano")
executor = AgentStepExecutor(configuration=configuration, agent=TestModel())

with nh.run(executor):
    # Natural functions called here use TestModel instead of a real LLM
    ...
```

## References

- [Quickstart](quickstart.md)
- [Design](design.md)
- [API Reference](api.md)
- [Providers](providers.md)
- [Roadmap](roadmap.md)
