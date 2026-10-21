# Operation feedback

Use `RejectOperation` when an operation must not execute but another choice can still accomplish the task.
Use `AbortStep` when the current Natural step must end.
Both decisions run before the operation body, after admission and any native-tool argument validation.
Earlier effects, validators, and trusted inspection hooks are not rolled back.

Expected domain findings usually belong in ordinary Python return values.
For example, a search helper can return an empty result and a checker can return typed findings.
Operation oversight is the connection point for deciding whether an inspected operation may execute; Core does not supply domain policies, approval products, or automatic repair strategies.

## Decisions and records

| Decision | Operation body | Same step | Observation |
| --- | --- | --- | --- |
| `Accept()` | Runs normally | Continues | Ordinary operation result. |
| `RejectOperation(reason, guidance=None)` | Does not start | May choose another operation | `OperationFinished.status == "rejected"`, with `rejection`; direct APIs raise `OperationRejectedError`. |
| `AbortStep(reason)` | Does not start | Ends; success cannot replace the stop | `OperationFinished.status == "aborted"`; `StepAbortedError` and a failed terminal record. |

`OperationRejectedError.rejection` and the operation record share the same `OperationRejection`.
Its `request` identifies the operation and step; `reason` and `guidance` are deliberately model-visible.
`model_feedback()` returns those public fields and execution-start information without exposing live tool arguments or diagnostic exceptions.
Reasons are nonempty strings; guidance is an optional string.

An inspector must return a decision normally.
Unexpected exceptions, invalid decisions, and notification failures are terminal, including exceptions whose classes otherwise represent retries or recoverable input errors.
`OperationDeliveryError.finished` retains the operation fact and `delivery_cause` retains its notification failure.
Successful delivery is an in-process notification attempt, not an external storage transaction.

## Synchronous and asynchronous model examples

This deterministic example verifies execution semantics without a provider service.
It is not evidence that a particular model reliably chooses a good alternative.

```py
import asyncio

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

import nighthawk as nh
from nighthawk.oversight import Accept, OperationDecision, Oversight, RejectOperation
from nighthawk.runtime import OperationRequest


effects: list[str] = []


def forbidden() -> int:
    effects.append("forbidden body")
    return 2


def inspect_operation(request: OperationRequest) -> OperationDecision:
    if request.expression == "forbidden()":
        return RejectOperation("This helper is unavailable.", "Evaluate 1 + 1 instead.")
    return Accept()


async def inspect_operation_async(request: OperationRequest) -> OperationDecision:
    await asyncio.sleep(0)
    return inspect_operation(request)


@nh.function
def calculate() -> int:
    """execute
    Calculate the requested value using a permitted operation and return it.
    """
    return 0


@nh.function
async def calculate_async() -> int:
    """execute
    Calculate the requested value using a permitted operation and return it.
    """
    return 0


def make_executor() -> nh.AgentStepExecutor:
    requests = 0

    async def respond(messages: list[ModelMessage], information: AgentInfo) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("nh_evaluate", {"expression": "forbidden()"}, "proposal")])
        assert requests == 2
        assert any(isinstance(part, ToolReturnPart) and part.outcome == "denied" for message in messages for part in message.parts)
        return ModelResponse(
            parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "return", "return_expression": "1 + 1"}}, "finish")]
        )

    return nh.AgentStepExecutor.from_configuration(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))


def run_sync_example() -> int:
    with (
        nh.run(make_executor()),
        nh.scope(
            task_references={"forbidden": forbidden},
            oversight=Oversight(inspect_operation=inspect_operation),
        ),
    ):
        return calculate()


async def run_async_example() -> int:
    with (
        nh.run(make_executor()),
        nh.scope(
            task_references={"forbidden": forbidden},
            oversight=Oversight(inspect_operation=inspect_operation_async),
        ),
    ):
        return await calculate_async()


assert run_sync_example() == 2
assert asyncio.run(run_async_example()) == 2
assert effects == []
```

The standard agent resolves return expressions inside its active output-processing loop as well.
If the model proposes `forbidden()` as its return expression, it can receive the same refusal and submit a different expression.
The resolved Python value then passes ordinary output validation and commit inspection without reevaluating the expression.
`inspect_step_commit` accepts only `Accept`, `Rewrite`, or `AbortStep`; a refused commit does not reopen inference.
Explicit `AbortStep` decisions propagate `StepAbortedError` from either inspector, unless terminal delivery raises a host exception.
Its `subject` identifies `"operation"` or `"step_commit"`.

## Custom executor example

This example extends the definitions above.
The executor owns its choice of the next operation and does not inspect or modify private failure state.

```py
from nighthawk.runtime import OperationRejectedError, Return, StepCandidate, StepRequest, evaluate_expression, evaluate_expression_async


class CorrectingExecutor:
    def run_step(self, *, request: StepRequest) -> StepCandidate:
        try:
            value = evaluate_expression(request.step_context, "forbidden()")
        except OperationRejectedError as refusal:
            assert refusal.rejection.reason == "This helper is unavailable."
            value = evaluate_expression(request.step_context, "1 + 1")
        return StepCandidate(outcome=Return(value))

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        try:
            value = await evaluate_expression_async(request.step_context, "forbidden()")
        except OperationRejectedError as refusal:
            assert refusal.rejection.reason == "This helper is unavailable."
            value = await evaluate_expression_async(request.step_context, "1 + 1")
        return StepCandidate(outcome=Return(value))


with (
    nh.run(CorrectingExecutor()),
    nh.scope(
        task_references={"forbidden": forbidden},
        oversight=Oversight(inspect_operation=inspect_operation_async),
    ),
):
    assert calculate() == 2
    assert asyncio.run(calculate_async()) == 2
assert effects == []
```

A custom executor may instead report that no permitted solution exists, using its ordinary result contract.
Refusal does not force an automatic retry or prove that a task was completed.
Application acceptance checks still determine whether the final result is useful and its effects acceptable.

## Ordering and backend boundaries

For a visible model response containing operations A, B, C, and an output proposal, a refusal of B preserves completed A, records B as rejected, and records C and the output as skipped.
Skipped calls do not run argument validators, inspectors, or bodies.
Their `blocked_by_operation_id` points to B; skipped output proposals have request kind `"output"`.
The model receives refusal and skipped-call feedback before choosing a new response.
An earlier output candidate in that same response cannot override a later refusal; its completed expression effects are retained without replay.
Tool calls follow emission order inside Nighthawk, including when the borrowed agent selects `early`.
Pydantic AI's native-output precedence still applies: under `early`, structured text can be evaluated before co-emitted function calls; other strategies prioritize the calls.
A refusal at that native-output boundary skips the co-emitted calls and consumes the ordinary output retry budget.

| Execution route | Refusal feedback and correction | Batch boundary |
| --- | --- | --- |
| Standard Pydantic AI tools | Denied tool return, correlated feedback, same agent run. | Remaining calls in the visible response are skipped; `early` follows emission order inside Nighthawk. |
| Standard structured/native output | Rejected candidate expressions return through output retry handling, before leaving the agent run. | The same visible response barrier applies when output and function calls coexist. |
| Codex and Claude Code MCP/SDK tools | Error indicator and structured refusal, followed by separately inspected tool requests; transport remains open. | Internal provider response batches are not exposed, so Core cannot identify or skip invisible siblings. |
| Direct operation API / custom executor | Catch `OperationRejectedError` and choose the next operation explicitly. | The executor owns call grouping and ordering. |
| Borrowed protocol agent without Pydantic AI capabilities | The borrowed agent owns model feedback and candidate-recovery integration; an unhandled refusal propagates. | No automatic loop or replay is provided. |

Python function composition also defines nesting.
An `AbortStep` permanently ends its target step.
An uncaught child failure fails the parent operation; a Python helper may explicitly catch that child failure and return an application result.
It cannot change the child's terminal fact or resume that child execution.
Run-wide cancellation is a separate host responsibility.

## Limits and interruption

Correction retains working values, object identity, active policy, request history, and usage counters.
Each additional Core/Pydantic AI-visible request passes the existing capability admission and accounting boundary.
Function-tool refusals consume ordinary request/tool-call usage; rejected output expressions also consume the configured output retry budget.
Host stops and exhausted limits remain effective even if another output candidate was accepted earlier in that response.

The aggregate `UsageMeter` is not an in-progress request gate.
Use the existing [request usage and admission hooks](host-integration.md#request-usage-and-admission) for controls between requests.
Coding-agent internal inference calls retain the backend's own limits and observability; this feature does not claim to observe them individually.
Unlimited configurations and arbitrary Python executor loops have no universal termination guarantee.

Interruption does not authorize detached processing or skipped cleanup.
Core retains its existing operation/terminal notification and asynchronous bridge responsibilities.
Application callbacks remain responsible for correctly shielding their own asynchronous cleanup under their execution framework's cancellation rules.

## Migration from 0.16

| Previous API | Replacement |
| --- | --- |
| Terminal `Reject(reason)` in either inspector | `AbortStep(reason)`. |
| `OversightRejectedError` | `StepAbortedError`. |
| Commit rejection caught as `ExecutionError` | Catch `StepAbortedError` for an explicit `AbortStep`; arbitrary commit-inspector failures retain the ordinary terminal error contract. |
| Operation rejection intended to permit another choice | Return `RejectOperation(reason, guidance=...)`; direct executors catch `OperationRejectedError`. |
| Terminal `failure_stage="oversight_rejection"` and `rejection_reason` | `failure_stage="oversight_abort"` and `abort_reason`. |
| Operation status `"rejected"` for a host termination | `"aborted"`; `"rejected"` now specifically identifies a recoverable operation refusal. |
| Assuming every operation record represents an attempted body | Check `status`, `phase`, and `evaluation_started`; later model calls may be `"skipped"`. |

The old symbols are removed; there is no compatibility mode that silently changes their meaning.
Operation and terminal records are host-owned evidence, not automatic snapshots of a mutable object graph or durable exactly-once storage.
