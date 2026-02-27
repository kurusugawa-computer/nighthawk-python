# Nighthawk Quickstart (Radical)

This quickstart focuses on the shortest path to writing effective Natural blocks in `nighthawk`.

## 0. One Executable Example

Use this as the baseline runtime shape.

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5.2")
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

Setup:

- Python `3.13+`
- Install dependencies: `uv sync --all-extras --all-groups`
- Credentials:
  - `OPENAI_API_KEY` for `openai-responses:*`
  - `CODEX_API_KEY` for `codex:default`

## 1. Authoring Rules (Must-follow)

1. Keep responsibilities split:
   - Natural: interpretation, synthesis, drafting.
   - Python: ordering, loops, validation, reliability boundaries.
2. Natural blocks must start with `natural\n` and no leading blank line.
3. Write one integrated instruction body; do not split into "bindings list" then "instructions".
4. Do not assume hidden continuity between blocks; persist values in Python locals and pass with `<name>`.
5. Writable bindings (`<:name>`) may be pre-declared or not:
   - Pre-declaration is optional.
   - Type annotations can help agent behavior and host-side validation/coercion.

Pre-declared writable binding:

```py
@nh.natural_function
def classify_priority(text: str) -> str:
    priority: str = "normal"
    """natural
    Read <text> and update <:priority> with one of: low, normal, high.
    """
    return priority
```

Not pre-declared writable binding:

```py
@nh.natural_function
def classify_priority(text: str):
    """natural
    Read <text> and set <:priority>.
    """
    # `priority` is intentionally introduced by <:priority>.
    return priority
```

## 2. Control Flow in Natural

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

## 3. Discoverability First (Docstrings and Types)

Built-in `nh_*` tools exist, but you usually do not need to instruct them explicitly in Natural text. The practical lever is better code discoverability.

Author for discoverability:

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

Inline f-string injection (locals/globals):

- You can inject Python-evaluated locals/globals directly into Natural text with an inline f-string block.
- Keep injected values trusted and repository-managed.

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

## 4. Review Checklist and References

Checklist:

- [ ] Natural and Python responsibilities are clearly separated.
- [ ] Each Natural block is one integrated instruction body.
- [ ] Cross-block data flow is explicit in Python locals.
- [ ] Side effects are bounded behind explicit, documented tools.
- [ ] Full coverage requirements are enforced by Python loops.
- [ ] Error behavior is explicit at the correct boundary.

References:

- `docs/README.md`
- `docs/design.md`
- `docs/roadmap.md`
