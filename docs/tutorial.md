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

### Step executor

A **step executor** is the strategy object that executes Natural blocks. It encapsulates the model, configuration, and backend. `AgentStepExecutor` is the built-in implementation backed by Pydantic AI. All Natural functions must be called inside a `with nh.run(step_executor):` context.

### Running the examples

All examples assume the [Quickstart](quickstart.md) setup:

```py
import nighthawk as nh

step_executor = nh.AgentStepExecutor.from_configuration(
    configuration=nh.StepExecutorConfiguration(model="openai-responses:gpt-5-mini"),
)

with nh.run(step_executor):
    ...  # Call natural functions here
```

See [Providers](providers.md) for model identifiers and backend options.

## 2. Providing Data to a Block

Two mechanisms supply data to a Natural block: **bindings** and **f-string injection**.

### Read bindings (`<name>`)

A read binding makes a Python value visible in the LOCALS section. The name cannot be rebound by the LLM.

```py
@nh.natural_function
def greet(user_name: str, language: str) -> str:
    greeting = ""
    """natural
    Compose a short greeting for <user_name> in <language> and set <:greeting>.
    """
    return greeting
```

`user_name` and `language` are read bindings — the LLM can read their values, but it cannot rebind the names. If a read binding holds a mutable object (e.g., a `list`), the LLM can mutate it in-place (see [Section 5](#the-carry-pattern)).

### Write bindings (`<:name>`)

A write binding allows the LLM to set a new value. The value is committed back into Python locals after the block.

Pre-declared (with type annotation and initial value):

```py
@nh.natural_function
def extract_sentiment(review: str) -> str:
    sentiment: str = "neutral"
    """natural
    Read <review> and update <:sentiment> with one of: positive, neutral, negative.
    """
    return sentiment
```

Annotation only (type without initial value):

```py
@nh.natural_function
def extract_topic(article: str) -> str:
    topic: str
    """natural
    Read <article> and set <:topic> to the main topic.
    """
    return topic
```

Not pre-declared:

```py
@nh.natural_function
def detect_language(text: str):
    """natural
    Read <text> and set <:language> to the detected language code.
    """
    # `language` is intentionally introduced by <:language>.
    return language
```

Type annotations on write bindings enable validation and coercion at commit time.

### Pydantic model write bindings

Write bindings can use Pydantic models for structured output with automatic validation:

```py
from pydantic import BaseModel

class ReviewVerdict(BaseModel):
    approved: bool
    reason: str
    risk_level: str

@nh.natural_function
def judge_review(review_data: str) -> ReviewVerdict:
    verdict: ReviewVerdict
    """natural
    Analyze <review_data> and produce a structured <:verdict>.
    Set approved, reason, and risk_level fields.
    """
    return verdict
```

When the LLM assigns a value to `verdict`, Nighthawk validates and coerces it to a `ReviewVerdict` instance. If the value does not conform to the model schema, a `ToolValidationError` is raised.

**How write bindings appear in the prompt.** A pre-declared write binding with an initial value appears in LOCALS like any other local (e.g., `sentiment: str = "neutral"`). An annotation-only or undeclared write binding does not appear in LOCALS — the LLM discovers it from the `<:name>` reference in the program text.

### Prompt appearance of bindings

Read and write bindings are rendered identically in the LOCALS section (e.g., `name: type = value`). The `<name>` vs `<:name>` distinction in the Natural program text is the signal that tells the LLM which names it may update. At runtime, Nighthawk enforces the distinction: read bindings block rebinding, while write bindings allow rebinding and commit values back to Python locals.

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

**Note:** To use literal angle brackets in program text without creating a binding, escape with a backslash: `\<name>` renders as `<name>` in the prompt without binding resolution. See [design.md Section 8.2.3](design.md#823-globals-summary) for details.

### Choosing between bindings and injection

| | f-string injection | `<name>` binding |
|---|---|---|
| Evaluation time | When Python evaluates the f-string literal | At LLM prompt construction |
| Appears in | Natural program text directly | Locals summary |
| Token control | Full — you decide the exact text | Governed by `context_limits` ([Section 6](#context-limits)) |
| LLM can mutate | No (text is baked in) | In-place only (e.g., `list.append()`) |
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

### Discoverability tips

- Use clear function names.
- Keep type annotations accurate.
- Write short docstrings that explain intent and boundaries.

### Keep locals minimal

Function parameters and local variables appear in LOCALS. Module-level names referenced via `<name>` that are _not_ in locals appear in GLOBALS. Nighthawk renders callable entries with their full signature and docstring intent — but only when type information is available.

When you pass a module-level callable as a function parameter with a generic type (`object`, `Any`, or no annotation), the name moves from GLOBALS to LOCALS and **its signature is erased**. The LLM cannot discover the correct arguments or return type.

Wrong — `fetch_data` loses its signature in LOCALS:

```py
from myapp import fetch_data

@nh.natural_function
async def summarize(query: str, fetch_data: object) -> str:
    result = ""
    """natural
    Use <fetch_data> to get data for <query> and set <:result>.
    """
    return result
```

```
<<<NH:LOCALS>>>
fetch_data: object = <non-serializable>
query: str = "latest news"
result: str = ""
<<<NH:END_LOCALS>>>
```

Correct — `fetch_data` keeps its full signature in GLOBALS:

```py
from myapp import fetch_data

@nh.natural_function
async def summarize(query: str) -> str:
    result = ""
    """natural
    Use <fetch_data> to get data for <query> and set <:result>.
    """
    return result
```

```
<<<NH:GLOBALS>>>
fetch_data: (query: str, max_results: int = 10) -> list[str]  # intent: Fetch data matching the query.
<<<NH:END_GLOBALS>>>
```

This principle extends beyond callables. Any module-level name that is stable across invocations — constants, classes, utility functions — should stay in GLOBALS via `<name>` read bindings rather than being pulled into LOCALS via parameters or local assignments. Reserve function parameters for data that genuinely varies per call.

**Note:** Nighthawk also provides `@nh.tool`, which registers functions via the model's native tool-calling interface. This path is reserved for cases that require `RunContext[StepContext]` access. Binding functions are preferred for all other uses because they incur no per-definition token overhead beyond a signature line in the prompt context. See [design.md Section 8.3](design.md#83-tools-available-to-the-llm) for the `@nh.tool` specification.

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

`summary: str` declares a type annotation without an initial value. The variable does not appear in the LOCALS section — the LLM discovers the binding from `<:summary>` in the program text. The type annotation enables validation when the LLM assigns a value.

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

### Error types

All Nighthawk exceptions inherit from `NighthawkError`. The most common exception in application code is `ExecutionError`, raised when a Natural block produces an invalid outcome, a disallowed outcome type, or a validation failure.

For the full exception hierarchy (`NaturalParseError`, `ToolEvaluationError`, `ToolValidationError`, `ToolRegistrationError`), see [design.md Section 13](design.md#13-error-handling).

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

- Use `<carry>` (read binding), not `<:carry>` (write binding). Read bindings prevent rebinding the name, which would break the caller's reference.
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

    # Replace the step executor entirely for a section
    with nh.scope(step_executor=another_executor):
        specialized_step(data)
```

Parameters:

- `step_executor_configuration`: replace the entire configuration.
- `step_executor_configuration_patch`: partially override specific fields.
- `step_executor`: replace the step executor entirely.
- `system_prompt_suffix_fragment`: append text to the system prompt for the scope.
- `user_prompt_suffix_fragment`: append text to the user prompt for the scope.

Use `step_executor_configuration_patch` for single-field changes (e.g., switching models). Use `step_executor_configuration` when all fields need explicit values. The context manager yields the resolved `StepExecutor` for the scope.

### Context limits

The LOCALS and GLOBALS sections are bounded by token and item limits configured via `StepContextLimits`. When a limit is reached, remaining entries are omitted and a `<snipped>` marker is appended.

```py
configuration = nh.StepExecutorConfiguration(
    model="openai-responses:gpt-5-mini",
    context_limits=nh.StepContextLimits(
        locals_max_tokens=4096,
        locals_max_items=50,
    ),
)
```

See [design.md Section 8.2](design.md#82-prompt-context) for the full specification.

### JSON rendering style

`StepExecutorConfiguration` also accepts `json_renderer_style`, which controls how values are rendered in prompt context and tool results (e.g., strict JSON vs annotated pseudo-JSON with omission markers). See [design.md Section 5.2](design.md#52-configuration) for available styles.

## 7. Async Natural Functions

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

The LLM calls `fetch_data` via `nh_eval`; Nighthawk detects the awaitable return value and awaits it automatically before returning the result to the LLM.

### Async and sync interoperability

Async natural functions can call sync binding functions, and sync natural functions can reference async binding functions. Nighthawk detects awaitable return values and handles them automatically:

- In async natural functions: awaitable results from `nh_eval` and `nh_assign` expressions are awaited before returning to the LLM.
- In sync natural functions: if the resolved return value is awaitable, execution fails (the caller must be async to await).

This means you can mix sync and async binding functions freely in async natural functions without special handling.

## Next steps

For observability, writing guidelines, binding function design, and testing, see [Practices](practices.md).

