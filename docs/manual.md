# Nighthawk Manual

This manual covers patterns and techniques for writing effective Natural blocks. For first steps, see [Quickstart](quickstart.md).

## 1. Bindings

Bindings connect Python values to Natural blocks. Two kinds:

### `<name>` — read binding

The value is visible inside the Natural block. The name will not be rebound after the block.

Mutable objects (lists, dicts, etc.) passed as read bindings **can be mutated in-place**. This is the basis of the carry pattern (Section 3).

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

Here `carry` is a read binding — the LLM can read it and mutate it in-place, but it cannot rebind the name.

### `<:name>` — write binding

Use `nh_assign` to set the value. The new value is committed back into Python locals after the block.

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

### What the LLM receives

When a Natural block executes, Nighthawk assembles a user prompt from three sections: the program text, a locals summary, and a globals summary. Calling `classify_priority("Server is on fire!")` from the example above produces the following user prompt:

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

- **`<<<NH:PROGRAM>>>`** contains the Natural program text (after sentinel removal and `textwrap.dedent`).
- **`<<<NH:LOCALS>>>`** lists all step locals alphabetically, rendered as `name: type = value`.
- **`<<<NH:GLOBALS>>>`** lists module-level names referenced via `<name>` that are not already in step locals.

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

## 2. Tool Selection

Three built-in tools handle all state operations:

| Tool | Purpose | When to use |
|------|---------|-------------|
| `nh_eval` | Evaluate an expression, return the result | Read a value, call a pure function |
| `nh_exec` | Execute an expression for its side effect | Mutate a mutable object in-place (e.g., `list.append()`) |
| `nh_assign` | Rebind a name to a new value | Set a write binding (`<:name>`) |

The LLM selects tools automatically based on the Natural block instructions. You rarely need to name tools explicitly in Natural text. The binding syntax (`<name>` vs `<:name>`) is the primary mechanism that guides tool selection:

- `<:name>` signals the LLM to use `nh_assign`.
- `<name>` on a mutable object signals the LLM to use `nh_exec` for mutation.

When you do need explicit guidance (e.g., calling a specific local function), name the function directly in the Natural text:

```py
@nh.natural_function
def compute() -> int:
    def calc(a: int, b: int) -> int:
        return a + b * 8

    result = 0
    """natural
    Compute <:result> by calling calc(1, 2).
    """
    return result
```

## 3. Cross-block Context (Carry Pattern)

Each Natural block executes independently — there is no implicit message history between blocks. To carry context across blocks, pass a mutable object as a read binding and let the LLM mutate it in-place.

### Basic continuity

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

The carry is a `list[str]` but any mutable object works — dicts, Pydantic models, custom classes.

When `step_2(carry)` executes (with `carry` already containing one entry from `step_1`), the LLM receives:

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

Note how the carry's current contents appear in the locals summary, giving the LLM visibility into prior context.

### Branching

Branch a session by copying the carry. Each branch continues independently.

```py
carry: list[str] = []
seed_step(carry)

# Branch at this point
carry_a = carry.copy()
carry_b = carry.copy()

result_a = branch_add(carry_a)       # diverges from here
result_b = branch_multiply(carry_b)  # independent path
```

### Design tips

- Use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent `nh_assign` from rebinding the name, which would break the caller's reference.
- Keep carry entries concise — they consume tokens in the locals summary on every subsequent step.

## 4. f-string Injection

Inline f-string blocks embed arbitrary Python expressions directly into Natural text. Any expression valid at the call site — locals, globals, member accesses, function call results — can be injected.

### Injecting locals and globals

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

Calling `choose_policy("Breaking: earthquake hits downtown")` produces the following user prompt. Notice that the f-string `{PROJECT_POLICY}` has already been evaluated into literal text in the program section, while `post` and `selected_policy` appear in the locals summary as bindings:

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

### Injecting member accesses and function results

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

### Injecting prior context (carry alternative)

When context is pre-formatted or the carry's locals summary footprint is too large, inject it directly:

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

### When to use f-string injection vs bindings

| | f-string injection | `<name>` binding |
|---|---|---|
| Evaluation time | When Python evaluates the f-string literal | At LLM prompt construction |
| Appears in | Natural program text directly | Locals summary |
| Token control | Full — you decide the exact text | Governed by `context_limits` |
| LLM can mutate | No (text is baked in) | Yes (via `nh_exec`) |
| Brace escaping | `{{` / `}}` to produce literal `{` / `}` | N/A |
| Best for | Static config, pre-formatted context, computed values | Mutable state, objects the LLM needs to inspect or modify |

## 5. Control Flow

Natural blocks can drive Python control flow (`return`, `break`, `continue`, `raise`) when the surrounding syntax allows it.

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

## 5.1. Frontmatter (Deny Directive)

A Natural block can optionally start with YAML frontmatter to restrict which outcome kinds are allowed. This is useful when you want to prevent the LLM from choosing certain control flow paths.

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

Frontmatter rules:

- Delimited by `---` lines (no indentation, no trailing whitespace).
- Must contain a `deny` key with a list of outcome kind names to exclude.
- Allowed deny values: `pass`, `return`, `break`, `continue`, `raise`.
- The deny list can only narrow the set allowed by syntactic context — it cannot add outcome kinds that the context does not permit (e.g., `break` outside a loop).

See [design.md Section 8.4](design.md#84-execution-contract-final-json) for the full specification.

## 6. Discoverability

The LLM discovers callable functions and their signatures from the locals/globals summary. Author for discoverability:

- Use clear function names.
- Keep type annotations accurate.
- Write short docstrings that explain intent and boundaries.
- Register boundary-crossing side effects with `@nh.tool`.

Registered tool:

```py
@nh.tool(name="add_points")
def add_points(run_context, *, base: int, bonus: int) -> int:
    """Return a deterministic sum for score calculation."""
    _ = run_context
    return base + bonus
```

Local helper (no `@nh.tool` registration):

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

When `compute_score_with_local_function()` executes, the LLM sees function signatures and docstrings rendered in the locals summary:

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

Callable values are rendered as their signature (with type annotations when available). The `# intent:` comment is the first line of the function's docstring. This is how the LLM discovers which functions are available and what they do.

When a Natural block references a module-level name via `<name>`, it appears in the globals section instead:

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

## 7. Scoped Configuration

Use `nh.scope()` to override execution settings for a block of code within an existing run. The scope generates a new `scope_id` while keeping the current `run_id`.

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

## 8. Authoring Rules

1. Keep responsibilities split:
   - Natural: interpretation, synthesis, drafting.
   - Python: ordering, loops, validation, reliability boundaries.
2. Natural blocks must start with `natural\n` and no leading blank line.
3. Write one integrated instruction body; do not split into "bindings list" then "instructions".
4. Cross-block data flow must be explicit. Use Python locals, the carry pattern, or f-string injection.
5. Write bindings (`<:name>`) may be pre-declared or not. Type annotations help agent behavior and host-side validation/coercion.

## 9. Review Checklist

- [ ] Natural and Python responsibilities are clearly separated.
- [ ] Each Natural block is one integrated instruction body.
- [ ] Cross-block data flow uses the carry pattern or f-string injection — no implicit continuity.
- [ ] Mutable context objects use `<name>` (read binding), not `<:name>` (write binding).
- [ ] Side effects are bounded behind explicit, documented tools.
- [ ] Full coverage requirements are enforced by Python loops.
- [ ] Error behavior is explicit at the correct boundary.

References:

- [Quickstart](quickstart.md)
- [Design](design.md)
- [API Reference](api.md)
- [Roadmap](roadmap.md)
