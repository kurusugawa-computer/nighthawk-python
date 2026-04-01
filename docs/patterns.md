# Patterns

> This page assumes you have completed [Natural blocks](natural-blocks.md) and [Runtime configuration](runtime-configuration.md).

This page covers how to apply Natural blocks in real workflows: outcomes, deny frontmatter, error handling, async, cross-block composition, resilience patterns, and common mistakes.

## Control flow and error handling

Natural blocks can drive Python control flow when the surrounding syntax allows it. The LLM returns a final outcome that determines what happens next. Full coverage requirements (e.g., processing every item in a list) are enforced by Python loops, not by Natural block instructions.

### Outcomes

<!-- outcome-kinds-table -->
| Outcome | Effect | Available when |
|---------|--------|----------------|
| `pass` | Continue to next Python statement | Always |
| `return` | Return from the surrounding function | Always |
| `break` | Break from the enclosing loop | Inside a loop |
| `continue` | Continue to the next loop iteration | Inside a loop |
| `raise` | Raise an exception | Always |
<!-- /outcome-kinds-table -->

```py
@nh.natural_function
def process_posts(posts: list[str]) -> list[str]:
    summaries: list[str] = []

    for post in posts:
        summary: str
        """natural
        Evaluate <post>.
        If this post means "stop now", break.
        If this post should be skipped, continue.
        Otherwise assign <:summary>.
        """
        summaries.append(summary)

    return summaries
```

`summary: str` declares a type annotation without an initial value. The variable does not appear in the LOCALS section -- the LLM discovers the binding from `<:summary>` in the program text. The type annotation enables validation when the LLM assigns a value.

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

All outcomes are allowed by default. A Natural block can start with YAML frontmatter to selectively disallow outcomes that the LLM should not choose.

There are two standard patterns:

**Post-block logic pattern** (`var = init; block; check; return var`) -- recommended for most cases:

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
    if not result:
        raise ValueError("Empty result")
    return result
```

`return` is denied to protect the post-block check. `pass` is allowed as a safe fallback -- write bindings keep their initial values, and the post-block check handles that case.

**Direct return pattern** (the block is the terminal expression):

```py
@nh.natural_function
def summarize(text: str) -> str:
    """natural
    ---
    deny: [pass, raise]
    ---
    Read <text> and return the summary.
    """
```

`pass` is denied because the block must produce a result. `return` is allowed because it is the block's purpose.

**Outcome reference:**

| Outcome | Effect | When to deny |
|---|---|---|
| `pass` | Normal completion -- bindings committed, execution continues | When every execution must produce an explicit control flow outcome |
| `return` | Function returns the LLM's value; post-block code is skipped | When post-block logic (validation, transformation) must execute |
| `raise` | Raises `ExecutionError` | When error handling belongs in Python |
| `break`/`continue` | Loop control (loops only) | When loop control belongs in Python |

See [Specification Section 8.4](specification.md#84-execution-contract-final-json) for the full frontmatter specification.

### Error handling

Natural blocks signal errors via the `raise` outcome. Error behavior should be explicit at the correct boundary -- the Natural block raises, and Python catches and handles. Catch errors on the Python side:

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

### Error types

All Nighthawk exceptions inherit from `NighthawkError`. The most common exception in application code is `ExecutionError`, raised when a Natural block produces an invalid outcome, a disallowed outcome type, or a validation failure.

For the full exception hierarchy (`NaturalParseError`, `ToolEvaluationError`, `ToolValidationError`, `ToolRegistrationError`), see [Specification Section 13](specification.md#13-error-handling).

## Async Natural functions

Natural functions can be async. The execution model is identical to sync natural functions, with two additions:

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

- Expressions evaluated by tools may use `await` (e.g., `await some_async_func()` inside an expression).
- Return values that are awaitable are automatically awaited before validation.
- The carry pattern and all other patterns work identically in async context.

Async binding functions work as expected:

```py
async def fetch_data(query: str) -> list[str]:
    """Fetch data matching the query from an external API."""
    ...

@nh.natural_function
async def analyze(query: str) -> str:
    result = ""
    """natural
    Use <fetch_data> to retrieve data for <query>, then set <:result> to a summary.
    """
    return result
```

The LLM calls `fetch_data` through a tool call expression; Nighthawk detects the awaitable return value and awaits it automatically before returning the result to the LLM.

### Concurrent execution

Async natural functions are ordinary coroutines, so you can run multiple Natural blocks concurrently with `asyncio.gather`:

```py
import asyncio

@nh.natural_function
async def classify(text: str) -> str:
    label: str = ""
    """natural
    Read <text> and set <:label> to one of: positive, negative, neutral.
    """
    return label

async def classify_batch(texts: list[str]) -> list[str]:
    return list(await asyncio.gather(*(classify(t) for t in texts)))
```

Each concurrent Natural block executes independently -- there is no shared message history or state between them. This makes `asyncio.gather` safe for Natural blocks that do not share mutable bindings.

### Async and sync interoperability

Async natural functions can call sync binding functions, and sync natural functions can reference async binding functions. Nighthawk detects awaitable return values and handles them automatically:

- In async natural functions: awaitable results from `nh_eval` and `nh_assign` expressions are awaited before returning to the LLM.
- In sync natural functions: if the resolved return value is awaitable, execution fails (the caller must be async to await).

This means you can mix sync and async binding functions freely in async natural functions without special handling.

**Failure mode:** if a sync natural function references an async binding function and the LLM calls it, the expression produces an awaitable that cannot be awaited in a sync context. Nighthawk raises an `ExecutionError`. To fix, make the natural function `async`.

## Cross-block composition

Since each Natural block executes independently ([one block, one task](natural-blocks.md#one-block-one-task)), cross-block context must be explicit.

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

Any mutable object works -- `list`, `dict`, Pydantic models, custom classes.

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

When the carry's locals summary footprint is too large, or context is pre-formatted, inject it directly via f-string ([f-string injection](natural-blocks.md#f-string-injection)):

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

- Use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent rebinding the name, which would break the caller's reference.
- Keep carry entries concise -- they consume tokens in the locals summary on every subsequent step.

## Resilience patterns

Natural blocks are non-deterministic by nature. Production deployments need strategies to handle transient failures, unstable outputs, and provider outages. The `nighthawk.resilience` module provides composable **function transformers** -- each takes a callable and returns a new callable with the same signature.

```py
from nighthawk.resilience import retrying, fallback, vote, timeout, budget, circuit_breaker
```

Import directly from `nighthawk.resilience`. Resilience primitives are not re-exported from the top-level `nighthawk` namespace.

### Retrying

Wrap a function to retry on failure. Uses [tenacity](https://tenacity.readthedocs.io/) internally. Defaults to retrying on `ExecutionError` with exponential backoff and jitter.

Decorator form -- create a resilient version of the function:
```py
from nighthawk.resilience import retrying

resilient_classify = retrying(attempts=3)(classify)
result = resilient_classify(text)
```

Iterator form -- retry a code block (tenacity pattern):
```py
from nighthawk.resilience import retrying

for attempt in retrying(attempts=3):
    with attempt:
        result = classify(text)
```

Customize which exceptions trigger retries and the backoff strategy:

```py
from tenacity import wait_fixed

resilient = retrying(
    attempts=5,
    on=(ExecutionError, TimeoutError),
    wait=wait_fixed(2),
)(classify)
```

### Fallback

Try multiple functions in order. The first success wins.

```py
from nighthawk.resilience import fallback

safe_classify = fallback(classify_gpt4, classify_mini, default="unknown")
result = safe_classify(text)
```

The `on` parameter controls which exceptions trigger fallback (default: `Exception`). All functions must have compatible signatures.

### Vote (majority voting)

Call a function multiple times and aggregate results. Useful for classification and judgment tasks where LLM outputs are inconsistent.

```py
from nighthawk.resilience import vote

voting_classify = vote(count=3)(classify)
label = voting_classify(text)
```

Async functions run concurrently via `asyncio.gather`. Sync functions run sequentially. Partial failures are tolerated: only successful results go to the `decide` function (default: `plurality` -- most common result). If fewer than `min_success` calls succeed (default: `ceil(count/2)`), the last exception is raised.

### Timeout

Enforce a time limit on function execution.

```py
from nighthawk.resilience import timeout

timed_classify = timeout(seconds=30)(classify)
result = timed_classify(text)
```

For async functions, uses `asyncio.timeout` (true cancellation). For sync functions, runs in a background thread -- note that the underlying thread continues after timeout. Also available as an async context manager:

```py
async with timeout(seconds=30):
    result = await slow_operation()
```

### Budget

Enforce token or monetary cost limits on wrapped functions. Requires an active `nh.run()` context (the run-scoped `UsageMeter` tracks cumulative usage automatically).

```py
from nighthawk.resilience import budget

safe_classify = budget(tokens=50_000, tokens_per_call=5_000)(classify)
result = safe_classify(text)
```

`tokens` caps cumulative usage across all calls; `tokens_per_call` caps a single call. Both are checked before and after each invocation. When a limit is breached, `BudgetExceededError` is raised -- combine with `fallback` to degrade gracefully:

```py
from nighthawk.resilience import budget, fallback, BudgetExceededError

composed = fallback(
    budget(tokens=50_000)(classify_gpt4),
    classify_mini,
    on=(BudgetExceededError,),
)
```

For monetary budgets, supply a `cost_function` that converts `RunUsage` to a float:

```py
from pydantic_ai.usage import RunUsage

def dollar_cost(usage: RunUsage) -> float:
    return usage.input_tokens * 3e-6 + usage.output_tokens * 15e-6

budgeted = budget(cost=1.00, cost_function=dollar_cost)(classify)
```

Outside a `nh.run()` context, the transformer is a no-op.

### Circuit breaker

Prevent repeated calls to a failing service. After `fail_threshold` consecutive failures, the circuit opens and rejects calls immediately with `CircuitOpenError`. After `reset_timeout` seconds, one probe call is allowed.

```py
from nighthawk.resilience import circuit_breaker, CircuitState

protected_api = circuit_breaker(fail_threshold=5, reset_timeout=60)(call_api)
protected_api.state    # CircuitState.CLOSED
protected_api.reset()  # manual reset
```

This is a **stateful** transformer (like `functools.lru_cache`). Each `circuit_breaker(...)` call creates independent state.

### Composition

All transformers produce callables with the original signature, so they compose by nesting. Read inside-out:

```py
robust_classify = fallback(
    retrying(attempts=2)(                      # 3. Retry the voted call
        vote(count=3)(classify_gpt4)           # 2. Vote 3x with GPT-4
    ),                                         # 1. Try GPT-4 first
    retrying(attempts=2)(classify_mini),       # 4. Fall back to mini
    default="unknown",                         # 5. Last resort
)

result = robust_classify(text)
```

Recommended composition order (innermost to outermost):

| Order | Transformer | Why |
|---|---|---|
| 1 | `timeout` | Bound each individual call |
| 2 | `budget` | Cap token or monetary cost |
| 3 | `vote` | Aggregate multiple bounded calls |
| 4 | `retrying` | Retry the aggregated operation |
| 5 | `circuit_breaker` | Protect against persistent failure |
| 6 | `fallback` | Switch to alternative on exhaustion |

### Caching LLM results

`nighthawk.resilience` does not provide a cache primitive. Natural functions are ordinary callables, so standard Python caching works directly:

```py
from functools import lru_cache

@lru_cache(maxsize=256)
@nh.natural_function
def classify(text: str) -> str:
    label: str = ""
    """natural
    Read <text> and set <:label> to one of: positive, negative, neutral.
    """
    return label
```

For TTL-based caching, use [cachetools](https://cachetools.readthedocs.io/). Place cache **outside** vote and retrying -- caching the voted/retried result avoids redundant LLM calls on repeated inputs.

## Common mistakes

<!-- common-mistakes-table -->
| Mistake | Why it breaks | Fix |
|---|---|---|
| Pass a callable as a parameter with generic type (`object`, `Any`) | Signature erased in LOCALS; LLM cannot discover arguments | Reference via `<name>` read binding so it appears in GLOBALS with full signature ([Keep locals minimal](natural-blocks.md#keep-locals-minimal)) |
| Use `<:carry>` (write binding) for mutable context | Rebinding breaks the caller's reference | Use `<carry>` (read binding); mutate in-place ([The carry pattern](#the-carry-pattern)) |
| Put two independent tasks in one block | Non-deterministic, hard to test, unclear contract | Split into two blocks connected by Python |
| Use Natural for deterministic computation | Wastes latency/cost, adds non-determinism | Use Python ([Responsibility split](natural-blocks.md#responsibility-split)) |
| Forget type annotations on write bindings | No validation or coercion at commit time | Always annotate `<:name>` bindings |
| Duplicate module-level constants as function parameters | Moves stable values from GLOBALS to LOCALS, wastes tokens | Reference via `<name>` read binding ([Keep locals minimal](natural-blocks.md#keep-locals-minimal)) |
| Try to "compile" a Natural block into deterministic Python | Judgment tasks cannot be reduced to static code; input space is unbounded | Keep the Natural block; use Python only for deterministic operations ([Philosophy](philosophy.md#why-evaluate-every-time)) |
| Add resilience logic inside a Natural block | LLM cannot reliably retry itself or manage timeouts | Wrap the Natural function call from Python using `nighthawk.resilience` ([Resilience patterns](#resilience-patterns)) |
<!-- /common-mistakes-table -->
