# Nighthawk guide for coding agents

Use this guide when writing Python applications with Nighthawk.
It contains the design principles, executable examples, and basic verification needed to start without reading other pages.
Follow the task-specific links when using advanced capabilities; the [specification](https://kurusugawa-computer.github.io/nighthawk-python/specification/) defines runtime behavior.

## Design from the judgment boundary

Nighthawk embeds Natural blocks in ordinary Python functions.
Each block invokes an LLM or coding agent with current Python values and returns through an explicit contract.
Python owns the surrounding control flow, state, and recovery.

Compose Natural and ordinary Python functions for actions, constraints, evaluation, and feedback using existing libraries where they fit.
Design functions so agents can discover them, understand their intent, inputs, outputs, effects, and failures, and reuse and improve them across tasks.
When contributing to Nighthawk Core, use [Design drivers](https://kurusugawa-computer.github.io/nighthawk-python/design-drivers/) to separate general execution needs from application-specific rules and development tooling.

Before implementing a feature, identify what requires interpretation and what can be guaranteed by explicit rules:

| Design question | Nighthawk approach |
| --- | --- |
| Can explicit rules determine the answer at acceptable maintenance burden? | Write Python for computation, I/O, validation, and known procedures. Use Natural for interpretation, classification, generation, and selection when rules are inadequate or too burdensome to maintain. |
| Is the input ambiguous or loosely structured? | Let Natural interpret it into a typed intermediate. Keep already structured inputs typed; do not recreate semantic interpretation with an expanding Python parser. |
| What must the application guarantee? | Enforce coverage, ordering, checks, retries, and effect handling in Python. For example, iterate over every item in Python when every item must be assessed. |
| Which choices require judgment? | Natural can choose relevant evidence, helpers, or follow-up assessments within the host's contract. Expose deterministic operations as Python helpers. |
| What result can the caller use? | Declare narrow output types, related fields in a Pydantic model, and an explicit representation for uncertainty when the task requires it. |
| How will the design be verified? | Test Python contracts deterministically and assess semantic correctness with representative real-model inputs. |

Give each block one coherent task with an observable result.
Split independent judgments so Python can coordinate and verify them separately.
One task can still involve autonomous investigation and several helper calls; it need not be a single classification or tool operation.
The host determines which results and effects are acceptable.

Natural judgments run for each invocation using the current input and context.
Do not replace an open-ended judgment with Python generated from a few examples merely to avoid model calls.
When explicit rules suffice at acceptable maintenance burden, implement them directly in Python.
Revisit this division using evaluation evidence as requirements and model capabilities change.
See [Philosophy](https://kurusugawa-computer.github.io/nighthawk-python/philosophy/) for the rationale and tradeoffs.

## A working template

Use Python 3.13+ and install Nighthawk with a provider dependency:

```sh
pip install nighthawk-python "pydantic-ai-slim[openai]"
```

Set `OPENAI_API_KEY`, then save and run this Python file:

```py
from typing import Literal

import nighthawk as nh


@nh.function
def classify_ticket(text: str) -> Literal["bug", "feature", "question"]:
    label: Literal["bug", "feature", "question"] = "question"
    """execute
    ---
    deny: [raise, return]
    ---
    Read <text> and set <:label> to bug, feature, or question.
    """
    return label


if __name__ == "__main__":
    with nh.run(execution_model="openai-responses:gpt-6-luna"):
        print(classify_ticket("The application crashes when opening a file."))
```

`@nh.function` transforms standalone strings beginning with `execute` or `decide <:output>` into executable blocks.
Use `execute` for operations and assignments; use `decide` for typed judgments, with Python controlling subsequent actions.
See [Typed decisions](https://kurusugawa-computer.github.io/nighthawk-python/decisions/) for the output, input, and failure contracts.
Define these functions in source files so Nighthawk can inspect their source.
`nh.run(execution_model=..., decision_model=...)` supplies the selected models and a fresh usage meter; `nh.run(executor)` accepts a custom executor.
A run must be active when a block executes.
Defining a function requires neither a run nor provider credentials.
Ordinary Python calls compose Natural functions within that run.

Start with a provider-backed executor for judgments using Python bindings and helpers.
Choose a coding-agent backend when the task needs filesystem access, commands, or autonomous investigation in a working directory.
Read [Quickstart](https://kurusugawa-computer.github.io/nighthawk-python/quickstart/) for setup problems and [Executors](https://kurusugawa-computer.github.io/nighthawk-python/executors/) before selecting a different backend.

## Bindings, outcomes, and context

- `<name>` requires an existing Python value and prioritizes its presentation to the model. It is not an access allowlist or a read-only object wrapper.
- `<:name>` declares a Python output slot whose committed value is validated. Annotate outputs explicitly, using `Literal`, typed collections, or Pydantic models for the contract.
- An `execute` block can finish with `pass`, `return`, or `raise`, and with `break` or `continue` inside a loop. `pass` continues with the next Python statement; `return` exits the surrounding function.
- Use `deny` frontmatter to prohibit outcomes that would bypass required Python work. The template denies `return` to preserve the Python return path and denies the explicit `raise` outcome; runtime and helper failures can still raise.
- Denying outcomes does not require a fresh assignment. A permitted `pass` can retain an output's initial value. Use a deliberate default or an unbound annotated output, and validate the result before dependent work.
- Each block starts a fresh model interaction without implicit message history. Carry essential inputs and results through Python parameters and return values.
- Python lexical values and scoped task references supply context. Prefer explicit arguments for essential dependencies and `scope(task_references=...)` for intentionally shared policy. Parent working values are not copied into children. Child name rebinding stays local; explicitly shared mutable objects retain identity.

A `decide` block requires one explicit answer and continues with the next Python statement.
Its initial output value cannot substitute for a missing answer.

The type boundary may sit inside a function: accept text that needs interpretation, then commit a typed result before deterministic work begins.
Types constrain the result's shape and range; they do not prove that its meaning is correct.

Treat Natural source, imported Markdown, prompt fragments, and declarations as trusted program assets under the host's [trust contract](https://kurusugawa-computer.github.io/nighthawk-python/specification/#3-hard-constraints), regardless of how they were authored or stored.
Revise and reuse programs within delegated authority; changing task acceptance criteria or execution policy requires authority to change those requirements.
Pass user data as binding values.
Inline f-string interpolation evaluates Python expressions before prompt construction and changes Natural source; reserve it for trusted configuration or program fragments.
Binding values still influence model judgment; keeping them out of source does not guarantee that a model ignores instructions in data.
Typed bindings do not sandbox a block or make execution transactional.
Helpers and object mutations can have effects before output validation, and those effects can survive failure.

For Natural text and declarations selected at runtime, follow [embedded authorization](https://kurusugawa-computer.github.io/nighthawk-python/embedded-execution/#authorize-the-execution): `execute_step` and `execute_step_async` do not invoke `FunctionCallHandler`.
Applying `@nh.function` to a generated function still requires [available Python source](https://kurusugawa-computer.github.io/nighthawk-python/specification/#812-supported-python-source-forms).
When an application needs revision provenance, follow [Record revision evidence](https://kurusugawa-computer.github.io/nighthawk-python/embedded-execution/#record-revision-evidence); terminal Natural text alone does not identify a complete revision.

For direct Natural returns, loop outcomes, multimodal inputs, or template composition, read [Natural blocks](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/) and [control flow patterns](https://kurusugawa-computer.github.io/nighthawk-python/patterns/#control-flow-and-error-handling) before implementation.

## Interpret first, then compute in Python

A quote request may describe quantities in ordinary language.
Natural interprets that language; Python applies the trusted price and decides whether interpretation is sufficient to proceed.
Save this example as `quotes.py`:

```py
from typing import Annotated

from pydantic import BaseModel, Field

import nighthawk as nh


class OrderRequest(BaseModel):
    quantity: Annotated[int | None, Field(ge=1, le=100)]


@nh.function
def interpret_order(message: str) -> OrderRequest:
    order: OrderRequest
    """execute
    Interpret the requested item quantity in <message> and set <:order>.
    Resolve ordinary number words and expressions such as a dozen.
    Use quantity=None when no single quantity is clear or it is outside 1 through 100.
    Do not guess a quantity for an ambiguous request.
    """
    return order


def quote_batch(messages: list[str], unit_price_cents: int) -> list[int]:
    if unit_price_cents < 0:
        raise ValueError("Unit price must be nonnegative")
    totals: list[int] = []
    for message in messages:
        order = interpret_order(message)
        if order.quantity is None:
            raise ValueError("Quantity needs clarification")
        totals.append(order.quantity * unit_price_cents)
    return totals
```

Call `quote_batch` inside `nh.run(execution_model="openai-responses:gpt-6-luna")`, using the run setup above.
For `["A dozen, please.", "Please send three."]` at 250 cents per item, the intended totals are `[3000, 750]`.
The host supplies the price; the model does not calculate or choose it.
The Python loop processes every input in order on success, while an unclear quantity stops the batch with an explicit error.
If the product requires collecting all errors and continuing, implement that policy in the Python loop.

This separates three questions: what the message means, whether the interpreted value satisfies the contract, and what the application does with it.
If the requirement changes to investigating only suspicious orders, Natural may choose which orders deserve follow-up within a separate investigation task.
Keep mandatory coverage and effect policy in Python in either design.

## Expose capabilities that fit the task

Binding functions are ordinary Python callables available to a block through its environment.
Reference a helper with `<helper_name>` and state when its result is useful.
It can perform I/O or deterministic computation in Python while Natural decides whether and how to use it.

Expose domain operations with precise signatures and short docstrings.
For example, `find_recent_orders(customer_id: str)` can encapsulate a fixed query and sorting rule, leaving the model to decide which returned order matches the user's description.
Keep fixed query construction in Python; expose parameters only for choices the judgment needs to make.
Natural functions are also callables and can serve as judgment helpers.

Use module-level helpers for stable capabilities and explicit parameters for invocation-specific data.
Reduce incidental locals and large previews before increasing context limits.
Presentation and callable metadata discovery are best-effort: a `<snipped>` preview does not mean the underlying value is missing.
Provide focused retrieval helpers for large data and make essential dependencies explicit.

Read [binding function design](https://kurusugawa-computer.github.io/nighthawk-python/natural-blocks/#designing-binding-functions) when designing a larger capability surface, and [cross-block composition](https://kurusugawa-computer.github.io/nighthawk-python/patterns/#cross-block-composition) before relying on inherited context or shared mutable state.

## Verify contracts and judgment separately

Install `pytest`, save the following as `test_quotes.py` beside `quotes.py`, and run `python -m pytest -q test_quotes.py`.
These tests use the real Natural transformation and output validation with scripted model results; they need no provider credentials.

```py
import pytest
from quotes import quote_batch

import nighthawk as nh
from nighthawk.testing import ScriptedExecutor, pass_response


def test_quote_batch_processes_each_message_and_uses_host_price():
    messages = ["A dozen, please.", "Please send three."]
    executor = ScriptedExecutor(
        responses=[
            pass_response(binding_name_to_value={"order": {"quantity": 12}}),
            pass_response(binding_name_to_value={"order": {"quantity": 3}}),
        ],
    )
    with nh.run(executor):
        assert quote_batch(messages, unit_price_cents=250) == [3000, 750]
    assert [call.step_locals["message"] for call in executor.calls] == messages


def test_quote_batch_stops_when_quantity_is_unclear():
    executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"order": {"quantity": None}})])
    with nh.run(executor), pytest.raises(ValueError, match="Quantity needs clarification"):
        quote_batch(["A few, please.", "Then three more."], unit_price_cents=250)
    assert len(executor.calls) == 1


def test_quote_batch_rejects_invalid_model_output():
    executor = ScriptedExecutor(responses=[pass_response(binding_name_to_value={"order": {"quantity": 0}})])
    with nh.run(executor), pytest.raises(nh.ExecutionError):
        quote_batch(["One, please."], unit_price_cents=250)
```

`ScriptedExecutor` supplies fixed response sequences and records calls.
Use `CallbackExecutor` when scripted responses need to depend on inputs.
Add checks for the relevant application effects, branches, and failure paths as the design grows.
To test control outcomes, import `return_response` and `raise_response` from `nighthawk.testing`.
`return_response("bug")` returns a literal Python value, and `raise_response("failed", error_type=ValueError)` requests an application exception using its resolved class.
For a block that denies those outcomes, assert `nh.ExecutionError` and verify that dependent work did not run.
Also test `pass_response()` without assignments when retaining an initial value could conceal a missing result.

The scripted quantity of 12 does not demonstrate that the prompt understands "a dozen".
Run separate, opt-in real-model tests with clear quantities, alternative phrasings, ambiguous requests, and out-of-range requests.
Assert the intended quantities, clarification behavior, and host-calculated totals; merely checking that a quantity has the right type or falls in range is insufficient.
For generated prose, check task criteria instead of exact wording.
Preserve failures as regression cases and repeat representative cases to assess variability.

Read [Verification](https://kurusugawa-computer.github.io/nighthawk-python/verification/) when adding real-model tests or diagnosing assembled prompts.

## Extend the application when the task requires it

Keep the following principles in mind, and read the linked contract before implementing the corresponding feature.

| When you need to... | Principle to preserve | Read before implementing |
| --- | --- | --- |
| Change models or inject capabilities for part of a workflow | `nh.scope(...)` composes settings within a run and restores the parent on exit. `execution_model=...` and `decision_model=...` select models independently; replacing the full configuration has broader effects. | [Runtime configuration](https://kurusugawa-computer.github.io/nighthawk-python/runtime-configuration/) |
| Retry, fall back, vote, or cache | Compose policy around ordinary Python calls. Catch `nh.ExecutionError` for runtime failures and application exception types for direct application failures. Reconcile existing effects before repeating work; retrying record storage must not replay execution. | [Resilience patterns](https://kurusugawa-computer.github.io/nighthawk-python/patterns/#resilience-patterns) |
| Run concurrent judgments or enforce timeouts | Natural functions can be `async def`; Python owns concurrency and shared-state coordination. Cancellation is cooperative, and cleanup can extend waiting. A synchronous timeout does not stop its worker. | [Async Natural functions](https://kurusugawa-computer.github.io/nighthawk-python/patterns/#async-natural-functions) and [timeouts](https://kurusugawa-computer.github.io/nighthawk-python/patterns/#timeout) |
| Refuse a function before its Python effects or record its completion | Host admission, block completion, and request observations are different boundaries. A completed block does not establish that its containing function succeeded. | [Host integration](https://kurusugawa-computer.github.io/nighthawk-python/host-integration/) |
| Track usage or admit work under a budget | Recorded usage can omit failed or hidden requests. Function-level budget checks are not a strict spending cap; concurrent or admitted work can overrun a threshold. | [Request usage and admission](https://kurusugawa-computer.github.io/nighthawk-python/host-integration/#request-usage-and-admission) |
| Give a block filesystem access, commands, or skills | Keep the task and output contract explicit. The host and backend own permissions, isolation, and external effects. | [Coding agent backends](https://kurusugawa-computer.github.io/nighthawk-python/coding-agent-backends/) |
| Use an unfamiliar interface or extend an executor | Check the documented signature and runtime contract rather than guessing from a name. | [API reference](https://kurusugawa-computer.github.io/nighthawk-python/api/) and [Specification](https://kurusugawa-computer.github.io/nighthawk-python/specification/) |

## Review the resulting design

Before considering an implementation complete, check:

- Natural handles judgments that depend on input and context; Python handles explicit rules and required orchestration.
- Each judgment has sufficient context, a usable typed result, and a defined response to uncertainty or failure.
- Required checks and effects cannot be bypassed by an unintended Natural outcome.
- State crosses blocks deliberately; output validation is not mistaken for isolation, rollback, or factual verification.
- Tests demonstrate the Python contract, and real-model evidence supports claims about semantic behavior.

When deciding between designs, apply the [philosophy](https://kurusugawa-computer.github.io/nighthawk-python/philosophy/): satisfy its invariants, then follow its defaults unless evidence supports their stated exceptions.
Its invariants are design obligations; the specification and evaluation evidence determine what behavior can be claimed.
Prefer the design whose judgment boundary, Python guarantees, and verification are easiest to explain and maintain.
