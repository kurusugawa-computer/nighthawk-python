# Nighthawk guide for coding agents

> Give this document to a coding agent developing Python code that uses Nighthawk. It is not a contributor guide for nighthawk-python itself. This is a derivative guide. If it conflicts with `specification.md`, the specification document wins.

You are expected to write, review, and debug Python code that uses Nighthawk as a senior engineer. Apply the rules below without waiting to be prompted.

## 1. Non-negotiables

- Python owns deterministic logic. Use Python for computation, control flow, I/O, validation, retries, state management, orchestration, and data shaping.
- Natural blocks are for semantic judgment. Use them for classification, interpretation, generation, ranking, and decisions that depend on context or world knowledge.
- One Natural block should do one task with one contract. If a block makes two independent decisions, split it.
- There is no implicit cross-block history. Persist state in Python values and pass it back explicitly.
- Natural blocks and imported markdown are trusted, repository-managed assets. Do not splice untrusted user input into Natural source text or markdown preprocessing. Pass untrusted data as bindings.
- Prefer explicit, typed write bindings. Runtime inference exists for unannotated write bindings, but new code should not rely on it.
- Keep outputs narrow and typed, especially when using coding agent backends. A block may do broad internal work, but the Python boundary should stay small.
- When prompt context is truncated and you see `<snipped>`, first reduce context surface or split the block. Increase `StepContextLimits` only after simplification fails.

## 2. First decision: should this be Natural at all?

Use Natural only when the block genuinely needs model judgment.

Use Python when the result is computable from explicit rules:

- parsing, filtering, arithmetic, sorting, schema validation
- deterministic routing and retries
- filesystem and network plumbing
- transforming one known structure into another

Use Natural when the block needs semantic interpretation:

- classify a report into categories
- summarize or rewrite text for a target audience
- extract structured meaning from messy language
- choose among options using contextual judgment

Default bias: if you can write the correct answer directly in Python, do not use Natural.

## 3. Second decision: which executor should this block use?

Nighthawk supports two different execution styles for Natural blocks. Choose per block, not per project.

| Use case | Preferred executor | Why |
|---|---|---|
| Bounded judgment, extraction, labeling, summarization, structured output | Pydantic AI provider-backed executor | Lower cost, lower latency, tighter surface area |
| Repository inspection, multi-file reasoning, command use, adaptive long-horizon work | Coding agent backend | The block becomes an autonomous agent execution with tools and its own reasoning loop |

Recommended default:

- Start with a Pydantic AI provider-backed executor for most blocks.
- Escalate only the blocks that truly need autonomous agent behavior to a coding agent backend.
- Do not default an entire workflow to coding agent backends just because one block is deep.

### Minimal setup

Install Nighthawk and a provider (adjust the extra for your provider):

```sh
pip install nighthawk-python "pydantic-ai-slim[openai]"
```

Every Natural function call must happen inside an `nh.run()` context. Without it, Nighthawk raises `NighthawkError: StepExecutor is not set`.

```py
import nighthawk as nh


@nh.natural_function
def summarize(text: str) -> str:
    summary: str = ""
    """natural
    ---
    deny: [raise, return]
    ---
    Read <text> and set <:summary> to a concise summary.
    """
    return summary


executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(
        model="openai-responses:gpt-5.4-mini",
    ),
)

with nh.run(executor):
    result = summarize("long document text")
```

See [Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/) for provider alternatives and credentials.

With coding agent backends, each Natural block is an autonomous agent execution. The agent may read files, run commands, and invoke skills inside the block. Python still owns the workflow, and only the declared outputs cross the boundary back to Python.

Example: mix a cheap classifier with a deep analysis step in one workflow.

```py
import nighthawk as nh


fast_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(
        model="openai-responses:gpt-5.4-mini",
    ),
)

deep_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(
        model="codex:default",
    ),
)


def search_repository(query: str) -> list[str]: ...


@nh.natural_function
def classify_ticket(text: str) -> str:
    label: str = ""
    """natural
    ---
    deny: [raise, return]
    ---
    Read <text> and set <:label> to one of: bug, feature, question.
    """
    return label


@nh.natural_function
def write_analysis_report(ticket_text: str, product_context: str) -> str:
    report: str = ""
    """natural
    ---
    deny: [raise, return]
    ---
    Read <ticket_text> and <product_context>.
    Analyze the issue, identify likely causes, and set <:report> to a detailed analysis.
    """
    return report


with nh.run(fast_executor):
    label = classify_ticket(ticket_text)
    with nh.scope(
        step_executor=deep_executor,
        implicit_references={"search_repository": search_repository},
    ):
        report = write_analysis_report(ticket_text, product_summary)
```

`implicit_references` can inject global helper functions as block capabilities.
Nested scopes still merge additively (set union by key).

## 4. The standard contract shape

Prefer the post-block logic pattern. Let the block write a typed value, then validate or transform it in Python.

```py
@nh.natural_function
def summarize(text: str) -> str:
    summary: str = ""
    """natural
    ---
    deny: [raise, return]
    ---
    Read <text> and set <:summary> to a concise summary.
    """
    if not summary.strip():
        raise ValueError("Summary must not be empty")
    return summary
```

Why this is the default:

- Python gets the final say on validation.
- The Natural block stays focused on judgment, not host control flow.
- Tests can lock down post-block behavior deterministically.

Use direct return only for leaf steps whose whole purpose is to return immediately:

```py
@nh.natural_function
def choose_title(text: str) -> str:
    """natural
    ---
    deny: [pass, raise]
    ---
    Read <text> and return a title.
    """
```

Structured output with Pydantic models:

```py
from pydantic import BaseModel


class TicketClassification(BaseModel):
    label: str
    confidence: float
    reasoning: str


@nh.natural_function
def classify_ticket_structured(text: str) -> TicketClassification:
    result: TicketClassification
    """natural
    ---
    deny: [raise, return]
    ---
    Read <text> and set <:result> to the classification.
    """
    return result
```

See [Natural blocks: Designing structured output](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/#designing-structured-output) for guidelines on model design.

## 5. State boundary and bindings

Rules:

- `<name>` is a read binding. The model can inspect the value but cannot rebind the name.
- `<:name>` is a write binding. The model sets a new top-level value that commits back into Python locals after the block. Always add type annotations to write bindings.
- Read bindings expose shared mutable objects. If the model mutates a bound list, dict, or object in place, the caller sees the mutation. Use this intentionally for the carry pattern, not casually.

This is why the carry pattern uses a read binding:

```py
@nh.natural_function
def step_1(carry: list[str]) -> int:
    result: int = 0
    """natural
    Set <:result> to 10.
    Append a one-line summary of what you did to <carry>.
    """
    return result
```

Additional rules:

- Bindings are simple identifiers only. `<name>` and `<:name>` do not take dotted paths.
- Dotted paths belong to internal tool expressions for attribute mutation, not to bindings.
- There is no hidden memory between blocks. If later blocks need state, return it, pass it, or mutate a shared object explicitly.

## 6. Block text, interpolation, and context

Natural blocks come in two forms:

- A function docstring beginning with `natural\n`
- An inline string literal statement beginning with `natural\n`, including inline f-strings

Docstring Natural blocks are literal. Inline f-string Natural blocks evaluate Python expressions before the model sees the prompt.

Use f-strings only for static configuration or already-shaped context:

```py
PROJECT_POLICY = ["cite assumptions", "be concise", "avoid speculation"]


@nh.natural_function
def choose_policy(post: str) -> str:
    selected_policy: str = ""
    f"""natural
    Read <post>.
    Available policies: {PROJECT_POLICY}
    Set <:selected_policy> to the single best policy.
    """
    return selected_policy
```

Do not inject untrusted raw text into Natural source. If input is user-controlled, pass it as a binding such as `<post>`.

## 7. Exposing functions and capabilities to the model

Rules:

- The model sees callable signatures from both LOCALS and GLOBALS.
- Put per-invocation data in function parameters. Put stable, reusable capabilities at module level.
- Do not annotate callable parameters as `object` or `Any` -- this erases the signature the model needs:

```py
@nh.natural_function
async def summarize(query: str, fetch_data: object) -> str:
    result: str = ""
    """natural
    Use <fetch_data> to get data for <query> and set <:result>.
    """
    return result
```

`fetch_data: object` hides useful type information. The model sees an unhelpful surface.

Prefer one of these:

- Expose a stable module-level helper through `<fetch_data>`
- Wrap complex operations in a smaller helper with a simple signature
- Keep callable parameters precisely typed if they truly must be local

Good binding functions have small signatures and clear first-line docstrings. Every extra parameter is another decision point for the model.

## 8. Control flow and failure handling

Natural blocks have five outcome kinds: `pass`, `return`, `break`, `continue`, and `raise`.

Use `deny` frontmatter to constrain outcomes the model should not choose.

Default patterns:

- Post-block logic pattern: `deny: [raise, return]`
- Direct-return pattern: `deny: [pass, raise]`

Error-handling rule:

- Let the model signal failure with `raise`.
- Catch failures in Python with `except nh.ExecutionError`, or expose explicit exception types if you want the block to raise them directly.

Async rule:

- Async natural functions can use async binding functions.
- If a sync natural function triggers an async binding function and gets an awaitable, Nighthawk raises `ExecutionError`.
- Fix that by making the natural function `async`.

Resilience rule:

- Keep retry, fallback, timeout, and circuit-breaker policy in Python, not inside Natural text.
- Import from `nighthawk.resilience` (not re-exported from `nighthawk`):

```py
from nighthawk.resilience import retrying

resilient_classify = retrying(attempts=3)(classify_ticket)

with nh.run(executor):
    label = resilient_classify(ticket_text)
```

See [Patterns: Resilience](https://kurusugawa-computer.github.io/nighthawk-python/patterns/#resilience-patterns) for `fallback`, `vote`, `timeout`, and `circuit_breaker`.

## 9. Context budget discipline

Prompt context is finite. When you see `<snipped>`, the marked data is truncated from the prompt but remains in Python memory -- the model can still reach it through binding functions. Fix context pressure in this order:

1. Remove irrelevant locals and globals from the function scope.
2. Split the block into smaller, focused blocks.
3. Pre-compute or pre-format context in Python before the block.
4. Replace complex helper signatures with simpler wrapper functions.
5. Increase `StepContextLimits` only as a last resort.

Do not raise limits as a first response to truncation. The root cause is usually too much state in scope.

## 10. Testing strategy

Test two layers separately.

| Layer | What to verify | Main tool |
|---|---|---|
| Deterministic Python shell around Natural blocks | control flow, validation, resilience, binding wiring, executor selection | `nighthawk.testing` |
| Natural block effectiveness | semantic correctness of the prompt against a real model | integration tests |

Mock-test rules:

- Use `ScriptedExecutor` for deterministic multi-step tests.
- Use `CallbackExecutor` when the response depends on the input.
- Inspect `executor.calls` to verify visible locals, globals, write bindings, and allowed outcomes.

Integration-test rules:

- Gate them behind an explicit opt-in.
- Assert on type, schema, range, or semantic class, not exact wording.
- For mixed-executor workflows, test both the cheap block and the deep block in the configuration they actually use.

## 11. Anti-patterns

| Anti-pattern | Why it is bad | Better pattern |
|---|---|---|
| Use Natural for deterministic computation | Higher cost, worse reliability, weaker tests | Write plain Python |
| Put two unrelated tasks in one block | Ambiguous contract, hard to test | Split into separate `@nh.natural_function` functions |
| Use coding agent backends for every block | Slow, expensive, oversized execution surface | Reserve them for deep autonomous steps |
| Omit type annotations on write bindings | No validation or coercion at commit time | Always annotate: `result: str = ""`, not just `result = ""` |
| Erase callable type with `object` | Model loses the signature it needs | Use precise types: `fetch: Callable[[str], Data]` |
| Solve truncation by only raising limits | Prompt bloat hides design problems | Shrink context first (section 9) |
| Depend on hidden cross-block memory | Blocks execute independently | Pass or return explicit state |
| Inject untrusted text into Natural source | Breaks the trust model | Pass user data through bindings: `<user_input>` |

## 12. References

Start here:

- [Natural blocks](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/) -- block anatomy, bindings, functions, binding function design, structured output
- [Executors](https://kurusugawa-computer.github.io/nighthawk-python/executors/) -- executor selection and configuration basics
- [Runtime configuration](https://kurusugawa-computer.github.io/nighthawk-python/runtime-configuration/) -- scoping, patching, context limits, and execution identity
- [Patterns](https://kurusugawa-computer.github.io/nighthawk-python/patterns/) -- outcomes, deny, async, carry, resilience, and common mistakes
- [Verification](https://kurusugawa-computer.github.io/nighthawk-python/verification/) -- testing and debugging

Canonical references:

- [Specification](https://kurusugawa-computer.github.io/nighthawk-python/specification/) -- canonical specification
- [Pydantic AI providers](https://kurusugawa-computer.github.io/nighthawk-python/pydantic-ai-providers/) -- model and provider configuration
- [Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/) -- backend-specific configuration and behavior
- [API reference](https://kurusugawa-computer.github.io/nighthawk-python/api/) -- public API surface
