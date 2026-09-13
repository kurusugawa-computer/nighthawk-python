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

`summary: str` declares an unbound output with a static contract.
Its declaration and contract remain discoverable even without a value preview.
If it remains unassigned, a later Python read raises normally.

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

Public library errors inherit from `NighthawkError`; ordinary application/helper exceptions and explicit Natural raises retain their own types.
Expected domain failures that the model should act on can instead be returned as ordinary helper results. The most common exception in application code is `ExecutionError`, raised when a Natural block produces an invalid outcome, a disallowed outcome type, or a validation failure.

For the full exception hierarchy (`NaturalParseError`, `ToolEvaluationError`, `ToolValidationError`, `ToolDeclarationError`), see [Specification Section 13](specification.md#13-error-handling).

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

Each concurrent Natural block has its own step context and no shared model message history.
Inherited Python objects and host accounting can still be shared; `asyncio.gather` is appropriate when those shared values are safe for concurrent use.
Tasks capture their selected context when created, including model selection and function-call handlers; awaiting a task elsewhere does not replace that context.

### Async and sync interoperability

Async natural functions can call sync binding functions, and sync natural functions can reference async binding functions. Nighthawk detects awaitable return values and handles them automatically:

- Tool expressions await async binding function results in both sync and async Natural functions before returning them to the LLM.
- A final return expression may produce an awaitable only in an async Natural function. A sync Natural function rejects an awaitable final return value.

You can therefore use async binding functions through tools without changing a synchronous Natural function into an async function.

**Failure mode:** if a sync Natural function selects a return expression such as `fetch_result()` and that expression produces an awaitable, Nighthawk raises `ExecutionError`. Make the Natural function `async` to permit an awaitable final return expression. This restriction does not apply when the model calls `fetch_result()` through a tool and writes its resolved result to a binding. See [async execution semantics](specification.md#85-async-execution-model).

## Cross-block composition

Each Natural block starts a fresh model interaction ([one block, one task](natural-blocks.md#one-block-one-task)).
Use Python arguments and returned values for essential inputs and results, inherited parent working state for intentionally shared task context, and scoped task references for injected capabilities.
Joined children inherit materialized parent associations at entry; shared objects remain live, while child name rebinding does not update the parent.
Children that outlive their parent have no inheritance guarantee.
The [environment contract](specification.md#813-parent-lifetime-and-expression-lifetime) defines the precise boundaries.

### Python iteration with Natural judgment

Here Python determines that each summary line is assessed once. Natural blocks perform the summarization and confidence judgments. Essential inputs are explicit parameters, so the assessor can also be called independently. Call the outer function inside `nh.run(executor)`.

```python
from typing import Annotated

from pydantic import Field

import nighthawk as nh


@nh.natural_function
async def assess_line(document: str, line: str) -> float:
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    """natural
    Assess how strongly <document> supports <line>.
    Set <:confidence> to a score from 0.0 to 1.0.
    """
    return confidence


@nh.natural_function
async def summarize_and_assess(document: str) -> list[tuple[str, float]]:
    summary = ""
    """natural
    Summarize <document> in <:summary>, with one claim per line.
    """
    assessments: list[tuple[str, float]] = []
    for line in summary.splitlines():
        confidence = await assess_line(document, line)
        assessments.append((line, confidence))
    return assessments
```

### Natural selects follow-up assessments

Here the parent chooses which claims need review.
The child receives the document and claim explicitly, and inherits the parent's materialized review policy.
Call these ordinary Natural functions inside `nh.run(executor)`:

```python
from typing import Annotated

from pydantic import Field

import nighthawk as nh


@nh.natural_function
async def assess_claim(document: str, claim: str) -> float:
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    """natural
    Assess how strongly <document> supports <claim>, applying <review_guidance>.
    Set <:confidence> to a score from 0.0 to 1.0.
    """
    return confidence


@nh.natural_function
async def review_claims(document: str, claims: list[str]) -> list[tuple[str, float]]:
    review_guidance = "Check quantitative and causal claims against direct evidence."
    assessments: list[tuple[str, float]] = []
    """natural
    Use <review_guidance> to choose claims from <claims> that need closer review.
    Call <assess_claim> with <document> and each selected claim, awaiting each result.
    Store the selected claims and their returned scores in <:assessments>.
    """
    return assessments
```

The parent step remains active until its chosen calls finish.
The policy does not need to pass through intermediate helper signatures, but `assess_claim` now has an inherited dependency: calling it independently requires supplying that policy, for example through task injection.
Prefer an explicit policy parameter when independent reuse matters.
The [nested execution acceptance example](specification.md#144-nested-execution-acceptance-example) isolates inheritance mechanics separately from this judgment task.

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
r1 = step_1(carry)  # carry now has 1 entry
r2 = step_2(carry)  # carry now has 2 entries
```

Any mutable object works -- `list`, `dict`, Pydantic models, custom classes.

When `step_2(carry)` executes, the LLM sees the carry's current contents in LOCALS:

<!-- prompt-example:carry-pattern -->
```py
Runtime inspection: __nighthawk_runtime_0.inspect(value).
Name discovery: __nighthawk_runtime_0.environment.discover(); named contracts: __nighthawk_runtime_0.environment.describe('name').
Application names, including inspect, retain their Python meaning. These runtime references cannot be assignment roots.
Displayed annotations are best-effort metadata and may be unresolved source spelling; they are not evaluated for presentation.

<<<NH:PROGRAM>>>
Read <carry> for prior context.
The carry says the previous result was 10.
Set <:result> to 20 (previous result plus 10).
Append a one-line summary of what you did to <carry>.
<<<NH:END_PROGRAM>>>

<<<NH:LOCALS>>>
Origin: python; name: carry
carry: list = ["Set result to 10."]
Origin: python; name: result; output contract: int
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

result_a = branch_add(carry_a)  # diverges from here
result_b = branch_multiply(carry_b)  # independent path
```

### Design tips

- Use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent rebinding the name, which would break the caller's reference.
- Keep carry entries concise -- they consume tokens in the locals summary on every subsequent step.

## Resilience patterns

Natural blocks are non-deterministic by nature. Production deployments need strategies to handle transient failures, unstable outputs, and provider outages. The `nighthawk.resilience` module provides composable **function transformers** -- each takes a callable and returns a new callable with the same signature.

```py
from nighthawk.resilience import retrying, fallback, vote, timeout, budget, circuit_breaker
```

Import directly from `nighthawk.resilience`.
After `import nighthawk as nh`, the module namespace is also available as `nh.resilience`.

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

`retrying` separates retry control into four roles:

- `on`: type-level retry eligibility.
- `retry_if`: content-level retry eligibility after `on` matches.
- `wait`: retry interval strategy.
- `on_retry`: side-effect hook when a retry is decided.

```py
from tenacity import wait_fixed

resilient = retrying(
    attempts=5,
    on=(ExecutionError, TimeoutError),
    retry_if=lambda exception: "transient" in str(exception).lower(),
    wait=wait_fixed(2),
    on_retry=lambda retry_state: logger.info("retrying", extra={"attempt": retry_state.attempt_number}),
)(classify)
```

Use only what you need. For simple judgments, `retrying(attempts=3)(classify)` is enough.
Choose retry boundaries around known effects: a failed block may already have mutated shared state or performed external operations.
A lifecycle-delivery failure never justifies replaying an execution; hosts retry storage separately and reconcile uncertain domain effects.

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

Set a timeout threshold for a function call; cleanup can extend the total waiting time.

```py
from nighthawk.resilience import timeout

timed_classify = timeout(seconds=30)(classify)
result = timed_classify(text)
```

For async functions, `asyncio.timeout` requests cooperative cancellation.
Natural notification cleanup is still joined, so cancellation-resistant cleanup can delay the timeout's propagation indefinitely.
For sync functions, the wrapper runs the function in a `ThreadPoolExecutor`; timeout does not stop the worker, and executor shutdown waits for it before `TimeoutError` reaches the caller.
The synchronous wrapper does not copy the caller's context variables: a worker executing Natural blocks must establish its own `nh.run()` and host scopes.
Use async Natural functions with the async timeout form when the caller's scoped controls should propagate normally.
The async context manager form is:

```py
async with timeout(seconds=30):
    result = await slow_operation()
```

### Budget

Check token or monetary thresholds around wrapped function calls using the active run's `UsageMeter`.
Outside `nh.run()`, the transformer performs no budget checks.

```py
from nighthawk.resilience import budget

safe_classify = budget(tokens=50_000, tokens_per_call=5_000)(classify)
result = safe_classify(text)
```

`tokens` checks recorded cumulative usage before invocation and after normal return; `tokens_per_call` checks the meter delta after normal return.
An optional `estimate_usage` can reject predicted over-limit work before invocation, but an estimate does not reserve usage or guarantee the final cost.
The meter records returned agent-run aggregates, so failed runs can consume unrecorded usage, and concurrent calls sharing a meter can contribute to the same measured delta.
These checks do not stop requests within an unfinished block or guarantee a strict spending cap.
For that earlier admission boundary, use [host request observations](runtime-configuration.md#request-usage-and-admission), keeping their accumulator separate from the meter.

When a checked threshold is exceeded, `BudgetExceededError` is raised.
Combine with `fallback` only when another execution is acceptable after any earlier effects and spending:

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
protected_api.state  # CircuitState.CLOSED
protected_api.reset()  # manual reset
```

This is a **stateful** transformer (like `functools.lru_cache`). Each `circuit_breaker(...)` call creates independent state.

### Composition

All transformers produce callables with the original signature, so they compose by nesting. Read inside-out:

```py
robust_classify = fallback(
    retrying(attempts=2)(  # 3. Retry the voted call
        vote(count=3)(classify_gpt4)  # 2. Vote 3x with GPT-4
    ),  # 1. Try GPT-4 first
    retrying(attempts=2)(classify_mini),  # 4. Fall back to mini
    default="unknown",  # 5. Last resort
)

result = robust_classify(text)
```

Recommended composition order (innermost to outermost):

| Order | Transformer | Why |
|---|---|---|
| 1 | `timeout` | Request cancellation or detect elapsed time; still wait for owned cleanup |
| 2 | `budget` | Check observed token or monetary thresholds around calls |
| 3 | `vote` | Aggregate multiple attempts |
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
| Assume every callable has complete discoverable metadata | Dynamic signatures or unavailable deferred annotations may be omitted | Keep essential dependencies explicit and provide a short callable docstring or ordinary helper; generic parameter annotations do not erase callable metadata ([Keep locals minimal](natural-blocks.md#keep-locals-minimal)) |
| Use `<:carry>` (write binding) for mutable context | Rebinding breaks the caller's reference | Use `<carry>` (read binding); mutate in-place ([The carry pattern](#the-carry-pattern)) |
| Put two independent tasks in one block | Non-deterministic, hard to test, unclear contract | Split into two blocks connected by Python |
| Use Natural for deterministic computation | Wastes latency/cost, adds non-determinism | Use Python ([Responsibility split](natural-blocks.md#responsibility-split)) |
| Rely on inference for structured outputs | Initial-value inference captures only the outer runtime type | Annotate `<:name>` bindings when element types or constraints matter |
| Duplicate module-level constants as function parameters | Moves stable values from GLOBALS to LOCALS, wastes tokens | Reference via `<name>` read binding ([Keep locals minimal](natural-blocks.md#keep-locals-minimal)) |
| Try to "compile" a Natural block into deterministic Python | Judgment tasks cannot be reduced to static code; input space is unbounded | Keep the Natural block; use Python only for deterministic operations ([Philosophy](philosophy.md#why-evaluate-every-time)) |
| Add resilience logic inside a Natural block | LLM cannot reliably retry itself or manage timeouts | Wrap the Natural function call from Python using `nighthawk.resilience` ([Resilience patterns](#resilience-patterns)) |
<!-- /common-mistakes-table -->
