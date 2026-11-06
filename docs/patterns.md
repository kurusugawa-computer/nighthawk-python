# Patterns

> This page assumes you have completed [Natural blocks](natural-blocks.md) and [Runtime configuration](runtime-configuration.md).

This page covers how to apply Natural blocks in real workflows: outcomes, deny frontmatter, error handling, async, cross-block composition, resilience patterns, and common mistakes.

## Control flow and error handling

Execution blocks can select a control outcome permitted by the surrounding Python syntax and their `deny` frontmatter.
Use Python loops for full coverage requirements, and deny `return`, `break`, or `continue` wherever those outcomes would bypass required work.
Decision blocks produce a value and continue; Python determines the subsequent control flow.

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
@nh.function
def process_posts(posts: list[str]) -> list[str]:
    summaries: list[str] = []

    for post in posts:
        summary: str
        """execute
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
@nh.function
def route_message(message: str) -> str:
    """execute
    Evaluate <message>.
    If immediate exit is required, set <:result> and return.
    If the message is invalid, raise with a clear reason.
    Otherwise set <:result> and pass.
    """
    return f"NEXT:{result}"  # reached when the block outcome is `pass`
```

### Deny frontmatter

An `execute` block allows every syntactically available outcome by default.
It can start with YAML frontmatter to disallow outcomes that would bypass required Python work.
This restricts accepted control outcomes; runtime and helper exceptions can still interrupt execution.
Decision blocks only complete with `pass` and do not accept control-flow frontmatter.

There are two standard patterns:

**Post-block logic pattern** (`var = init; block; check; return var`) -- recommended for most cases:

```py
@nh.function
def must_produce_result(text: str) -> str:
    result = ""
    """execute
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
@nh.function
def summarize(text: str) -> str:
    """execute
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
| `raise` | Raises the selected exception type, defaulting to `Exception` | When error handling belongs in Python |
| `break`/`continue` | Loop control (loops only) | When loop control belongs in Python |

See [Specification Section 8.4](specification.md#84-execution-contract-final-json) for the full frontmatter specification.

### Error handling

Natural blocks signal domain failures via the `raise` outcome.
The selected exception reaches Python unchanged; without a selected type, it is an ordinary `Exception`.
The broad handler below catches that default, but also catches other ordinary exceptions.
Prefer a specific domain exception when the caller must distinguish an expected refusal from an execution failure, as in the next example.

<!-- error-example:default_raise -->
```py
@nh.function
def validate(data: str) -> str:
    result = ""
    """execute
    Validate <data>. If invalid, raise with a clear reason.
    Otherwise set <:result> to "valid".
    """
    return result


try:
    validate("corrupted-input")
except Exception as error:
    print(f"Validation failed: {error}")
```
<!-- /error-example:default_raise -->

### Custom exception types

When a Natural block references exception types visible as Python locals or globals, those types become available as `raise_error_type` options:

<!-- error-example:custom_raise -->
```py
class InputError(Exception):
    pass


@nh.function
def strict_validate(data: str) -> str:
    result = ""
    """execute
    Validate <data>. If invalid, raise an <InputError>.
    Otherwise set <:result> to "valid".
    """
    return result


try:
    strict_validate("bad")
except InputError as error:
    print(f"Input error: {error}")
```
<!-- /error-example:custom_raise -->

### Error types

Nighthawk's public error contracts share `NighthawkError`; ordinary application/helper exceptions and explicit Natural raises retain their own types.
Catch the concrete error for recovery: belonging to this hierarchy alone does not tell you whether the step can continue or earlier effects can be retried safely.
See [Error handling](specification.md#13-error-handling) for operation, execution, Harness, and Session boundaries.
Expected domain failures that the model should act on can instead be returned as ordinary helper results.
Recoverable operation failures, including invalid values supplied to an assignment, return structured errors to the model rather than ending the block immediately.
Invalid outcomes and output values rejected during finalization raise `ExecutionError`, carrying the failure stage and original exception.

Handle only the failure stage for which the application has a recovery policy:

<!-- error-example:output_validation -->
```py
from nighthawk.core.errors import ExecutionError


@nh.function
def extract_count(text: str) -> int:
    count: int = 0
    """execute
    Read <text> and set <:count> to the number of items.
    """
    return count


try:
    extract_count("several items")
except ExecutionError as error:
    if error.step_failed.failure_stage != "binding_validation":
        raise
    print("Output validation failed; review the input before retrying.")
```
<!-- /error-example:output_validation -->

The original validation exception remains available as `error.step_failed.original_exception` and `error.__cause__`.
Earlier helper calls and mutations may already have taken effect, so a retry requires an explicit recovery decision.
Independent `validate_output_bindings()` calls instead raise `BindingValidationError`; see [Embedded execution](embedded-execution.md#reusing-binding-processing).

## Async Natural functions

Natural functions can be async. The execution model is identical to sync natural functions, with two additions:

```py
@nh.function
async def summarize_async(text: str) -> str:
    result = ""
    """execute
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


@nh.function
async def analyze(query: str) -> str:
    result = ""
    """execute
    Use <fetch_data> to retrieve data for <query>, then set <:result> to a summary.
    """
    return result
```

The LLM calls `fetch_data` through a tool call expression; Nighthawk detects the awaitable return value and awaits it automatically before returning the result to the LLM.

### Concurrent execution

Async natural functions are ordinary coroutines, so you can run multiple Natural blocks concurrently with `asyncio.gather`:

```py
import asyncio


@nh.function
async def classify(text: str) -> str:
    label: str = ""
    """execute
    Read <text> and set <:label> to one of: positive, negative, neutral.
    """
    return label


async def classify_batch(texts: list[str]) -> list[str]:
    return list(await asyncio.gather(*(classify(t) for t in texts)))
```

Each concurrent Natural block has its own step context and no shared model message history.
Explicit Python references and host accounting can still be shared; `asyncio.gather` is appropriate when those shared values are safe for concurrent use.
Tasks capture their selected context when created, including model selection and function-call handlers; awaiting a task elsewhere does not replace that context.

### Async and sync interoperability

Async natural functions can call sync binding functions, and sync natural functions can reference async binding functions. Nighthawk detects awaitable return values and handles them automatically:

- Tool expressions await async binding function results in both sync and async Natural functions before returning them to the LLM.
- A final return expression may produce an awaitable only in an async Natural function. A sync Natural function rejects an awaitable final return value.

You can therefore use async binding functions through tools without changing a synchronous Natural function into an async function.

**Failure mode:** if a sync Natural function selects a return expression such as `fetch_result()` and that expression produces an awaitable, Nighthawk raises `ExecutionError`. Make the Natural function `async` to permit an awaitable final return expression. This restriction does not apply when the model calls `fetch_result()` through a tool and writes its resolved result to a binding. See [async execution semantics](specification.md#85-async-execution-model).

## Cross-block composition

Each Natural block starts a fresh model interaction ([one block, one task](natural-blocks.md#one-block-one-task)).
Use Python arguments and returned values for essential inputs and results, and scoped task references for deliberately shared context and capabilities.
Children do not receive a parent's working values automatically; explicitly shared objects remain live, while child name rebinding does not update the parent.
Hosts establish explicit inputs and execution scope for independent work that outlives a parent step.
The [environment contract](specification.md#813-shared-values-and-expression-lifetime) defines the precise boundaries.

### Python iteration with Natural judgment

Here Python determines that each summary line is assessed once. Natural blocks perform the summarization and confidence judgments. Essential inputs are explicit parameters, so the assessor can also be called independently. Call the outer function inside `nh.run(executor)`.

```python
from typing import Annotated

from pydantic import Field

import nighthawk as nh


@nh.function
async def assess_line(document: str, line: str) -> float:
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    """execute
    Assess how strongly <document> supports <line>.
    Set <:confidence> to a score from 0.0 to 1.0.
    """
    return confidence


@nh.function
async def summarize_and_assess(document: str) -> list[tuple[str, float]]:
    summary: str = ""
    """execute
    ---
    deny: [return]
    ---
    Summarize <document> in <:summary>, with one claim per line.
    """
    assessments: list[tuple[str, float]] = []
    for line in summary.splitlines():
        confidence = await assess_line(document, line)
        assessments.append((line, confidence))
    return assessments
```

The summarization block denies `return` because returning its string would skip the assessment loop and violate the outer function's list contract.
The typed output `summary: str` and the function's return annotation describe different boundaries.

### Natural selects follow-up assessments

Here the parent chooses which claims need review.
The child receives the document and claim as arguments, and the review policy through an explicit task-reference scope.
Call these ordinary Natural functions inside `nh.run(executor)`:

```python
from typing import Annotated

from pydantic import Field

import nighthawk as nh


@nh.function
async def assess_claim(document: str, claim: str) -> float:
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    """execute
    Assess how strongly <document> supports <claim>, applying <review_guidance>.
    Set <:confidence> to a score from 0.0 to 1.0.
    """
    return confidence


@nh.function
async def review_claims(document: str, claims: list[str]) -> list[tuple[str, float]]:
    review_guidance = "Check quantitative and causal claims against direct evidence."
    assessments: list[tuple[str, float]] = []
    with nh.scope(task_references={"review_guidance": review_guidance}):
        """execute
        Use <review_guidance> to choose claims from <claims> that need closer review.
        Call <assess_claim> with <document> and each selected claim, awaiting each result.
        Store the selected claims and their returned scores in <:assessments>.
        """
    return assessments
```

The parent step remains active until its chosen calls finish.
The policy does not need to pass through intermediate helper signatures, but `assess_claim` has an explicit scoped dependency: calling it independently requires supplying `review_guidance` through task references.
Prefer an explicit policy parameter when independent reuse matters.
The [nested execution acceptance example](specification.md#144-nested-execution-acceptance-example) isolates explicit argument passing separately from this judgment task.

### The carry pattern

Pass a mutable object as a read binding and let the LLM mutate it in-place:

```py
@nh.function
def step_1(carry: list[str]) -> int:
    result = 0
    """execute
    Set <:result> to 10.
    Append a one-line summary of what you did to <carry>.
    """
    return result


@nh.function
def step_2(carry: list[str]) -> int:
    result = 0
    """execute
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

result_a = branch_add(carry_a)  # diverges from here
result_b = branch_multiply(carry_b)  # independent path
```

### Design tips

- Use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent rebinding the name, which would break the caller's reference.
- Keep carry entries concise -- they consume tokens in the locals summary on every subsequent step.

## Resilience patterns

Recovery is application-owned Python control flow.
Choose retry, fallback, cancellation, and caching boundaries according to effects already performed and the task's acceptance criteria.
A failed Natural step may have changed shared objects or performed external actions.
A lifecycle delivery failure requires reconciling storage separately; replaying the task does not repair its notification.

### Retrying

Use Tenacity directly when a repeatable operation benefits from bounded retries.
Install it in the application with `uv add tenacity` and select the exceptions and stopping rule explicitly.
For a classification function whose failures are safe to replay:

```py
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from nighthawk.core.errors import ExecutionError

for attempt in Retrying(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    retry=retry_if_exception_type(ExecutionError),
    reraise=True,
):
    with attempt:
        label = classify(text)
```

For async calls use `AsyncRetrying` and `async for`.
Do not retry terminal host refusals by widening authority or replacing the active executor.

### Fallback

Express alternatives with ordinary calls and exception handling.
The caller owns whether another attempt is acceptable after any earlier effects and spending.

```py
from nighthawk.core.errors import ExecutionError


def classify_with_fallback(text: str) -> str:
    try:
        return classify_primary(text)
    except ExecutionError:
        return classify_secondary(text)
```

### Vote

`nighthawk.parts.voting` supplies a judgment Part for repeated sampling and result aggregation.
It works with ordinary callables, independently of Natural, Harness, and Session.

```py
from nighthawk.parts.voting import vote

voting_classify = vote(count=3)(classify)
label = voting_classify(text)
```

Only successful results reach `decide`, whose default is `plurality`.
See the [API reference](api.md#nighthawk.parts.voting) for concurrency, failure, and cancellation semantics.

### Timeout and termination

Use Python's cooperative cancellation for asynchronous work:

```py
import asyncio

async with asyncio.timeout(30):
    label = await classify_async(text)
```

Cancellation requests do not prove that external work has ceased, and owned cleanup can extend the wait.
Synchronous Python calls need a host-owned process or another termination mechanism when cessation is required.
Harness exposes termination reports and unresolved cleanup through `nighthawk.harness.termination` and `RunHandle`.
See [host runtime obligations](agent/runtime.md) for the guarantees applications must supply.

### Resource accounting and admission

`nighthawk.core.usage.UsageMeter` accumulates immutable `ExecutionUsage` observations; it does not authorize requests or enforce spending.
Adapters translate provider usage into request, input-token, output-token, and total-token counts.
Provider-specific details remain in the adapter's `executor_result` on lifecycle records.

Harness allocation controls admission and records consumption:

```py
from nighthawk.harness.resources import bounded_resource_allocation


def allocation_for_run(identity):
    return bounded_resource_allocation(
        identity=f"allocation-{identity.run_id}",
        maximum_executor_step_count=20,
        maximum_total_token_count=50_000,
    )
```

Pass this factory to `Runner(resource_allocation_factory=...)`.
A token bound checks already observed usage before the next mediated request; an individual request can cross the remaining threshold.
Unknown, interrupted, and provider-billed usage can differ from observed usage.
Applications own monetary conversion, reservations, and any strict provider-side limits.
See [request usage and admission](host-integration.md#request-usage-and-admission) for custom integrations.

### Service recovery and caching

Applications can use a circuit-breaker library around the service they own.
Its failure classification, shared state, probe concurrency, and cleanup behavior belong to that service boundary.

Ordinary Python caching can retain accepted results of pure functions:

```py
from functools import lru_cache

import nighthawk as nh


@lru_cache(maxsize=256)
@nh.function
def classify(text: str) -> str:
    label: str = ""
    """execute
    Read <text> and set <:label> to one of: positive, negative, neutral.
    """
    return label
```

The application decides cache keys, expiry, invalidation, and whether model or policy changes require recomputation.

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
| Replace a contextual judgment with rules inferred from a few examples | The rules may fail on inputs or contexts the examples did not cover | Evaluate the proposed rules against representative cases; use Python where explicit rules suffice and retain Natural where interpretation is needed ([Responsibility split](natural-blocks.md#responsibility-split)) |
| Add resilience logic inside a Natural block | LLM cannot reliably retry itself or manage timeouts | Compose recovery around the Natural function with ordinary Python and selected libraries ([Resilience patterns](#resilience-patterns)) |
<!-- /common-mistakes-table -->
