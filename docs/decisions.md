# Typed decisions

Use `execute` to describe operations and intermediate assignments.
Use `decide` to answer a typed question, then use Python to branch, retry, or act on its result.
Both blocks live inside ordinary `@nh.function` functions and share validation, commit oversight, execution identity, and terminal delivery.

Install Nighthawk and the provider dependency:

```sh
uv add nighthawk-python 'pydantic-ai-slim[openai]>=2.58.0,<2.59'
```

Configure credentials using Pydantic AI's provider configuration, or pass a caller-owned `OpenAIDecisionsModel`.
The host retains ownership of supplied clients.

## One function, two kinds of work

```python
from typing import Annotated

from pydantic import Field

import nighthawk as nh


@nh.function
async def summarize_and_assess(document: str) -> tuple[str, float]:
    claims: list[str] = []
    summary: str = ""
    """execute
    Extract the factual claims from <document> into <:claims>.
    Summarize <claims> in <:summary>.
    Check <summary> against <document> and revise <:summary> as needed.
    """

    support_probability: Annotated[float, Field(ge=0, le=1)] = 0.0
    """decide <:support_probability>
    Does <document> support every factual claim in <summary>?
    """
    return summary, support_probability


async def main(document: str) -> tuple[str, float]:
    with nh.run(
        execution_model="openai-responses:gpt-6-luna",
        decision_model="openai-decisions:gpt-6-luna",
    ):
        return await summarize_and_assess(document)
```

An `execute` body can reference multiple outputs and assign the same output repeatedly.
Each `<:name>` authorizes that output; it does not itself perform an assignment.
Tools make intermediate assignments, and the runtime validates the selected final values before writing them back into Python.

A `decide` header declares exactly one output.
Its body contains the judgment and criteria, with `<name>` references to the evidence.
Do not ask it to write a value, call `nh_assign`, select tools, or return from the Python function.
Nighthawk passes the body as Pydantic AI instructions, obtains the typed answer, and commits it to the declared destination.
A successful decision continues with the next Python statement.

Initializing an output helps ordinary Python type checkers understand the function.
The initial value is never a fallback answer: a missing answer, refusal, unsupported type, or invalid response fails the step.
An annotation without an initial value also works, but static type checkers cannot infer the assignment performed by the DSL.

## Standard output contracts

Pydantic AI's decision model owns the mapping from Python/Pydantic types to questions.
Use its standard contracts, including booleans, `Literal` or `Enum` choices, bounded probabilities, and described rubric levels.
A single output binding can hold a Pydantic model with several questions:

```python
from typing import Literal

from pydantic import BaseModel, Field


class Assessment(BaseModel):
    """Assess the evidence before publication."""

    supported: bool = Field(description="Does the evidence support every claim?")
    action: Literal["publish", "review"] = Field(description="Which action is appropriate?")


@nh.function
def assess(document: str, summary: str) -> Assessment:
    assessment: Assessment
    """decide <:assessment>
    Assess <summary> using <document> as the evidence.
    """
    return assessment  # pyright: ignore[reportUnboundVariable]
```

The block body frames the judgment; model docstrings and field descriptions supply the structured questions.
Unsupported output schemas fail before the provider request.
Nighthawk does not translate unsupported decisions into free-form execution or silently switch models.
See the upstream [decision model contracts](https://pydantic.dev/docs/ai/models/decision/) and [OpenAI Decisions integration](https://pydantic.dev/docs/ai/models/openai/#decisions-api) for supported type semantics.

## Inputs and scoped settings

Decision evidence consists of the complete values of explicit input bindings.
Values must support Pydantic JSON serialization, or be supported top-level Pydantic AI multimodal content, including image evidence.
Unsupported serialization fails; Nighthawk does not truncate the evidence or replace it with a preview.
Implicit locals, globals, output initial values, execution prompt templates, prompt suffixes, context builders, and scoped action tools are not included.
An output value can be evidence when explicitly declared as a read with `<name>`.

`execution_model` and `decision_model` inherit independently from the nearest enclosing scope.
The decorator does not fix either model.
Omission or `nh.UNSET` inherits; `None` clears a model selection.
No model is selected by default, and only executing an unconfigured block kind requires it.

```python
configuration = nh.StepExecutorConfiguration(
    decision_model="openai-decisions:gpt-6-luna",
    decision_model_settings={"decision_boolean_threshold": 0.8},
)

with nh.run(step_executor_configuration=configuration):
    result = assess(document, summary)
    with nh.scope(decision_model=another_decision_model):
        another_result = assess(document, summary)
```

`model_settings` applies to execution models; `decision_model_settings` applies to decision models.
A supplied decision model may retain its own provider settings and defaults.
Scoped Pydantic AI capabilities still observe the model request.
Decision requests reject action tools and non-decision model selections introduced by ordinary request hooks.

## Results and evidence

Normal function results and assigned variables remain ordinary typed values.
A lifecycle record exposes the native Pydantic AI `AgentRunResult` for each completed model run:

```python
records: list[nh.lifecycle.StepFinished] = []

with nh.run(decision_model="openai-decisions:gpt-6-luna"):
    with nh.scope(lifecycle=nh.lifecycle.StepLifecycle(on_step_finished=records.append)):
        assessment = assess(document, summary)

for record in records:
    if record.block_kind == "decide" and record.model_result is not None:
        print(record.execution_reference.step_execution_id)
        print(record.model_result.response.provider_details)
        print(record.model_result.usage)
```

`provider_details` retains the upstream confidence, probability distributions, and unrounded scores where available.
Their interpretation depends on the output type; confidence is not a general probability that a judgment is correct.
Each loop iteration and nested decision has its own `step_execution_id`.
The native result remains on terminal records if later commit inspection or writeback fails.
A model failure before an `AgentRunResult` exists leaves `model_result=None`.
The usage meter aggregates completed model runs and does not invent usage for failed calls.

Commit oversight can accept, reject, or replace a validated decision under the common runtime contract.
A successful decision commit must retain its one output.
Earlier effects from an `execute` block are not rolled back when a later decision fails.

## Embedded decisions and verification

Hosts using explicit namespaces can select `StepDefinition(..., block_kind="decide")` with one output binding and its type.
The trusted program is the judgment body without a header.
`execute_step` and `execute_step_async` retain their normal validation, writer, oversight, and lifecycle behavior.
Custom executors receive `StepRequest.block_kind` and `input_binding_name_to_value`; a decision candidate must explicitly contain its answer and use `Pass`.

Deterministic tests in `tests/runtime/test_decisions.py` exercise the real OpenAI SDK and Pydantic AI adapter against a local HTTP transport.
They cover typed questions, full evidence, multiple execution assignments, settings, missing/refused/invalid answers, and detailed results after writeback failure.
The opt-in live test is `NIGHTHAWK_OPENAI_INTEGRATION_TESTS=1 uv run pytest -q tests/integration/test_openai_decisions.py` and requires credentials and Decisions API access.
These tests establish runtime contracts; they do not measure judgment quality or establish access to the live beta API.
