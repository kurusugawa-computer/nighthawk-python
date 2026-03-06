# Nighthawk Manual

This manual covers patterns and techniques for writing effective Natural blocks. For first steps, see `docs/quickstart.md`.

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
| Evaluation time | At function call (Python-side) | At LLM prompt construction |
| Appears in | Natural program text directly | Locals summary |
| Token control | Full — you decide the exact text | Governed by `context_limits` |
| LLM can mutate | No (text is baked in) | Yes (via `nh_exec`) |
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

## 7. Authoring Rules

1. Keep responsibilities split:
   - Natural: interpretation, synthesis, drafting.
   - Python: ordering, loops, validation, reliability boundaries.
2. Natural blocks must start with `natural\n` and no leading blank line.
3. Write one integrated instruction body; do not split into "bindings list" then "instructions".
4. Cross-block data flow must be explicit. Use Python locals, the carry pattern, or f-string injection.
5. Writable bindings (`<:name>`) may be pre-declared or not. Type annotations help agent behavior and host-side validation/coercion.

## 8. Review Checklist

- [ ] Natural and Python responsibilities are clearly separated.
- [ ] Each Natural block is one integrated instruction body.
- [ ] Cross-block data flow uses the carry pattern or f-string injection — no implicit continuity.
- [ ] Mutable context objects use `<name>` (read binding), not `<:name>` (write binding).
- [ ] Side effects are bounded behind explicit, documented tools.
- [ ] Full coverage requirements are enforced by Python loops.
- [ ] Error behavior is explicit at the correct boundary.

References:

- `docs/design.md`
- `docs/roadmap.md`
