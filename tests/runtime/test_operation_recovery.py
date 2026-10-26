from __future__ import annotations

import asyncio
import json
from typing import Any

import anyio
import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, Tool
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.profiles import ModelProfile

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFinished, StepLifecycle
from nighthawk.oversight import AbortStep, Accept, Oversight, RejectOperation, StepAbortedError
from nighthawk.runtime import (
    OperationDeliveryError,
    OperationError,
    OperationFinished,
    OperationRejectedError,
    OperationRejection,
    OperationRequest,
    Pass,
    StepCandidate,
    StepRequest,
    assign_expression_async,
    evaluate_expression,
    evaluate_expression_async,
)
from tests.support.contexts import new_context
from tests.support.executors import StubExecutor


@pytest.mark.parametrize("asynchronous_execution", [False, True])
@pytest.mark.parametrize("asynchronous_inspection", [False, True])
def test_custom_executor_recovers_without_restarting_the_step(asynchronous_execution: bool, asynchronous_inspection: bool) -> None:
    effects: list[str] = []
    operations: list[OperationFinished] = []
    terminals: list[StepFinished] = []
    refusals: list[OperationRejectedError] = []
    commits: list[nh.oversight.StepCommit] = []

    def forbidden() -> None:
        effects.append("forbidden")

    def inspect(request: OperationRequest) -> Accept | RejectOperation:
        return RejectOperation("This operation is unavailable.", "Use 7 instead.") if request.expression == "forbidden()" else Accept()

    async def inspect_async(request: OperationRequest) -> Accept | RejectOperation:
        await asyncio.sleep(0)
        return inspect(request)

    def commit(value: nh.oversight.StepCommit) -> Accept:
        commits.append(value)
        return Accept()

    class Executor:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            evaluate_expression(request.step_context, "effects.append('first')")
            with pytest.raises(OperationRejectedError) as caught:
                evaluate_expression(request.step_context, "forbidden()")
            refusals.append(caught.value)
            value = evaluate_expression(request.step_context, "7")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"value": value})

        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            await evaluate_expression_async(request.step_context, "effects.append('first')")
            with pytest.raises(OperationRejectedError) as caught:
                await evaluate_expression_async(request.step_context, "forbidden()")
            refusals.append(caught.value)
            value = await evaluate_expression_async(request.step_context, "7")
            return StepCandidate(outcome=Pass(), binding_name_to_value={"value": value})

    @nh.function
    def workflow() -> int:
        value: int = 0
        """execute
        Set <:value> using the permitted operation.
        """
        return value

    @nh.function
    async def workflow_async() -> int:
        value: int = 0
        """execute
        Set <:value> using the permitted operation.
        """
        return value

    with (
        nh.run(Executor()),
        nh.scope(
            task_references={"effects": effects, "forbidden": forbidden},
            oversight=Oversight(inspect_operation=inspect_async if asynchronous_inspection else inspect, inspect_step_commit=commit),
            lifecycle=StepLifecycle(on_operation_finished=operations.append, on_step_finished=terminals.append),
        ),
    ):
        assert (asyncio.run(workflow_async()) if asynchronous_execution else workflow()) == 7
    assert effects == ["first"]
    assert [operation.status for operation in operations] == ["succeeded", "rejected", "succeeded"]
    assert len({operation.request.operation_id for operation in operations}) == 3
    assert len({operation.request.execution_reference.step_execution_id for operation in operations}) == 1
    assert operations[1].rejection is refusals[0].rejection
    assert operations[1].original_exception is refusals[0]
    assert not operations[1].evaluation_started
    assert len(commits) == len(terminals) == 1
    assert isinstance(terminals[0], StepCompleted)
    assert terminals[0].assigned_binding_name_to_value == {"value": 7}


def test_rejected_assignment_never_evaluates_or_mutates_the_target() -> None:
    class Record(BaseModel, validate_assignment=True):
        value: int = 1

    record = Record()
    effects: list[str] = []
    context = new_context(record=record, effects=effects)

    async def execute() -> None:
        with pytest.raises(OperationRejectedError):
            await assign_expression_async(context, "record.value", "(effects.append('evaluated'), 9)[1]")
        with nh.scope(oversight=None):
            assert await evaluate_expression_async(context, "record") is record

    with nh.run(StubExecutor()), nh.scope(oversight=Oversight(inspect_operation=lambda request: RejectOperation("Do not update this item."))):
        asyncio.run(execute())
    assert record.value == 1 and effects == []


@pytest.mark.parametrize("failure_kind", ["invalid_decision", "retry", "operation_error", "rejected_error"])
def test_inspection_failures_never_become_recoverable_feedback(failure_kind: str) -> None:
    context = new_context()
    operations: list[OperationFinished] = []

    def inspect(request: OperationRequest) -> Any:
        if failure_kind == "invalid_decision":
            return "reject"
        if failure_kind == "retry":
            raise ModelRetry("Broken inspector")
        if failure_kind == "operation_error":
            raise OperationError(message="Broken inspector", phase="inspection", evaluation_started=False, guidance="Do not retry.")
        raise OperationRejectedError(OperationRejection(request, "An exception is not a decision."))

    async def execute() -> None:
        with pytest.raises(Exception) as caught:
            await evaluate_expression_async(context, "7")
        with nh.scope(oversight=None), pytest.raises(type(caught.value)) as repeated:
            await evaluate_expression_async(context, "8")
        assert repeated.value is caught.value

    with (
        nh.run(StubExecutor()),
        nh.scope(oversight=Oversight(inspect_operation=inspect), lifecycle=StepLifecycle(on_operation_finished=operations.append)),
    ):
        asyncio.run(execute())
    assert len(operations) == 1 and operations[0].status == "failed"
    assert operations[0].rejection is None and not operations[0].evaluation_started


@pytest.mark.parametrize("delivery_failure", ["exception", "retry", "rejection", "cancellation"])
def test_rejection_delivery_failure_stops_the_step_and_retains_the_rejection(delivery_failure: str) -> None:
    context = new_context()
    operations: list[OperationFinished] = []

    async def finished(operation: OperationFinished) -> None:
        operations.append(operation)
        await asyncio.sleep(0)
        if delivery_failure == "cancellation":
            raise asyncio.CancelledError("Delivery interrupted")
        if delivery_failure == "retry":
            raise ModelRetry("Broken notification")
        if delivery_failure == "rejection":
            assert operation.rejection is not None
            raise OperationRejectedError(operation.rejection)
        raise ValueError("Storage unavailable")

    async def execute() -> None:
        expected = asyncio.CancelledError if delivery_failure == "cancellation" else OperationDeliveryError
        with pytest.raises(expected) as caught:
            await evaluate_expression_async(context, "7")
        if isinstance(caught.value, OperationDeliveryError):
            assert caught.value.finished is operations[0]
            assert isinstance(caught.value.__cause__, OperationRejectedError)
        with pytest.raises(expected) as repeated:
            await evaluate_expression_async(context, "8")
        assert repeated.value is caught.value

    with (
        nh.run(StubExecutor()),
        nh.scope(
            oversight=Oversight(inspect_operation=lambda request: RejectOperation("Host reason")),
            lifecycle=StepLifecycle(on_operation_finished=finished),
        ),
    ):
        asyncio.run(execute())
    assert len(operations) == 1
    assert operations[0].status == "rejected" and operations[0].rejection is not None
    assert operations[0].rejection.reason == "Host reason"


@pytest.mark.parametrize("route", ["evaluation", "assignment", "native", "return", "native_output"])
def test_standard_model_receives_refusal_and_corrects_the_same_step(route: str) -> None:
    requests = 0
    bodies: list[str] = []
    operations: list[OperationFinished] = []
    terminals: list[StepFinished] = []
    request_usage: list[int] = []

    def forbidden() -> int:
        bodies.append("forbidden")
        return 1

    def inspect(request: OperationRequest) -> Accept | RejectOperation:
        if request.tool_name == "forbidden" or request.expression == "forbidden()":
            return RejectOperation("Host policy refused this operation.", "Use the constant 7.")
        return Accept()

    async def before_request(context: Any, request_context: Any) -> Any:
        request_usage.append(context.usage.requests)
        return request_context

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            if route == "native_output":
                return ModelResponse(parts=[TextPart(json.dumps({"result": {"kind": "return", "return_expression": "forbidden()"}}))])
            if route == "native":
                return ModelResponse(parts=[ToolCallPart("forbidden", {}, "refused")])
            if route == "evaluation":
                return ModelResponse(parts=[ToolCallPart("nh_evaluate", {"expression": "forbidden()"}, "refused")])
            if route == "assignment":
                return ModelResponse(parts=[ToolCallPart("nh_assign", {"target_path": "temporary", "expression": "forbidden()"}, "refused")])
            return ModelResponse(
                parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "return", "return_expression": "forbidden()"}}, "refused")]
            )
        feedback = [part for message in messages for part in message.parts if isinstance(part, (ToolReturnPart, RetryPromptPart))]
        assert feedback
        text = str([part.content for part in feedback])
        assert "Host policy refused this operation." in text and "Use the constant 7." in text
        assert operations[0].request.operation_id in text
        if route not in ("return", "native_output"):
            assert any(isinstance(part, ToolReturnPart) and part.outcome == "denied" for part in feedback)
        if route == "native_output":
            return ModelResponse(parts=[TextPart(json.dumps({"result": {"kind": "return", "return_expression": "7"}}))])
        return ModelResponse(
            parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "return", "return_expression": "7"}}, "finish")]
        )

    @nh.function
    async def workflow() -> int:
        """execute
        Return a permitted value.
        """
        return 0

    profile = ModelProfile(supports_json_schema_output=True, default_structured_output_mode="native") if route == "native_output" else None
    executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond, profile=profile)))
    with (
        nh.run(executor),
        nh.scope(
            tools=[forbidden],
            task_references={"forbidden": forbidden},
            capabilities=[Hooks(before_model_request=before_request)],
            oversight=Oversight(inspect_operation=inspect),
            lifecycle=StepLifecycle(on_operation_finished=operations.append, on_step_finished=terminals.append),
        ),
    ):
        assert asyncio.run(workflow()) == 7
    assert requests == 2 and request_usage == [0, 1] and bodies == []
    assert [operation.status for operation in operations] == ["rejected", "succeeded"]
    assert len(terminals) == 1 and isinstance(terminals[0], StepCompleted)
    assert operations[0].request.execution_reference == operations[1].request.execution_reference == terminals[0].execution_reference


@pytest.mark.parametrize("end_strategy", ["early", "graceful", "exhaustive"])
def test_rejected_batch_skips_later_validation_bodies_and_output(end_strategy: Any) -> None:
    events: list[str] = []
    operations: list[OperationFinished] = []
    requests = 0

    def record(value: int) -> int:
        events.append(f"body:{value}")
        return value

    async def validate(context: Any, value: int) -> None:
        events.append(f"validate:{value}")

    async def inspect(request: OperationRequest) -> Accept | RejectOperation:
        await asyncio.sleep(0)
        if request.argument_name_to_value.get("value") == 2:
            return RejectOperation("Value 2 is unavailable.", "Use 4.")
        return Accept()

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        output_name = information.output_tools[0].name
        if requests == 1:
            return ModelResponse(
                parts=[
                    *(ToolCallPart("record", {"value": value}, str(value)) for value in (1, 2, 3)),
                    ToolCallPart(output_name, {"result": {"kind": "return", "return_expression": "record(99)"}}, "stale-output"),
                ]
            )
        feedback = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        assert any(part.outcome == "denied" for part in feedback)
        assert "skipped" in str([part.content for part in feedback])
        return ModelResponse(parts=[ToolCallPart(output_name, {"result": {"kind": "return", "return_expression": "record(4)"}}, "finish")])

    @nh.function
    async def workflow() -> int:
        """execute
        Record permitted values and return the final value.
        """
        return 0

    executor = nh.AgentStepExecutor.from_agent(agent=Agent(FunctionModel(respond), deps_type=nh.StepContext, end_strategy=end_strategy))
    with (
        nh.run(executor),
        nh.scope(
            tools=[Tool(record, args_validator=validate)],
            task_references={"record": record},
            oversight=Oversight(inspect_operation=inspect),
            lifecycle=StepLifecycle(on_operation_finished=operations.append),
        ),
    ):
        assert asyncio.run(workflow()) == 4
    assert requests == 2
    assert events == ["validate:1", "body:1", "validate:2", "body:4"]
    assert [operation.status for operation in operations] == ["succeeded", "rejected", "skipped", "skipped", "succeeded"]
    assert all(operation.blocked_by_operation_id == operations[1].request.operation_id for operation in operations[2:4])
    assert all(not operation.evaluation_started for operation in operations[1:4])


@pytest.mark.parametrize("end_strategy", ["early", "graceful", "exhaustive"])
def test_earlier_output_is_discarded_after_rejection_without_replaying_its_effects(end_strategy: Any) -> None:
    effects: list[str] = []
    requests = 0

    def forbidden() -> None:
        raise AssertionError("A refused body cannot run")

    def inspect(request: OperationRequest) -> Accept | RejectOperation:
        return RejectOperation("Use another method.") if request.tool_name == "forbidden" else Accept()

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        expression = "(effects.append('once'), 1)[1]" if requests == 1 else "7"
        parts = [
            ToolCallPart(information.output_tools[0].name, {"result": {"kind": "return", "return_expression": expression}}, f"output-{requests}")
        ]
        if requests == 1:
            parts.append(ToolCallPart("forbidden", {}, "refused"))
        return ModelResponse(parts=parts)

    @nh.function
    async def workflow() -> int:
        """execute
        Return a permitted value.
        """
        return 0

    executor = nh.AgentStepExecutor.from_agent(agent=Agent(FunctionModel(respond), deps_type=nh.StepContext, end_strategy=end_strategy))
    with nh.run(executor), nh.scope(tools=[forbidden], task_references={"effects": effects}, oversight=Oversight(inspect_operation=inspect)):
        assert asyncio.run(workflow()) == 7
    assert effects == ["once"] and requests == 2


@pytest.mark.parametrize("replace_with_retry", [False, True])
def test_capability_cannot_replace_rejection_with_success_or_different_feedback(replace_with_retry: bool) -> None:
    requests = 0

    def forbidden() -> int:
        raise AssertionError("A rejected body cannot run")

    async def replace_result(context: Any, *, call: Any, tool_def: Any, args: Any, result: Any) -> object:
        if replace_with_retry:
            raise ModelRetry("Unrelated replacement feedback")
        return 42

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(parts=[ToolCallPart("forbidden", {}, "refused")])
        parts = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
        assert parts[-1].outcome == "denied"
        content = parts[-1].content
        assert isinstance(content, dict) and content["reason"] == "Host feedback"
        return ModelResponse(parts=[ToolCallPart(information.output_tools[0].name, {"result": {"kind": "pass"}}, "finish")])

    @nh.function
    async def workflow() -> None:
        """execute
        Choose a permitted method.
        """

    executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with (
        nh.run(executor),
        nh.scope(
            tools=[forbidden],
            capabilities=[Hooks(after_tool_execute=replace_result)],
            oversight=Oversight(inspect_operation=lambda request: RejectOperation("Host feedback")),
        ),
    ):
        asyncio.run(workflow())
    assert requests == 2


def test_repeated_rejections_preserve_request_accounting_and_host_stop() -> None:
    requests = 0
    observed_usage: list[int] = []
    operations: list[OperationFinished] = []
    stop = RuntimeError("Host request limit reached")

    def forbidden() -> None:
        raise AssertionError("Rejected bodies cannot run")

    async def before_request(context: Any, request_context: Any) -> Any:
        observed_usage.append(context.usage.requests)
        if context.usage.requests == 3:
            raise stop
        return request_context

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        return ModelResponse(parts=[ToolCallPart("forbidden", {}, f"attempt-{requests}")])

    @nh.function
    async def workflow() -> None:
        """execute
        Choose a permitted operation.
        """

    executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
    with (
        nh.run(executor),
        nh.scope(
            tools=[forbidden],
            capabilities=[Hooks(before_model_request=before_request)],
            oversight=Oversight(inspect_operation=lambda request: RejectOperation("Unavailable")),
            lifecycle=StepLifecycle(on_operation_finished=operations.append),
        ),
        pytest.raises(nh.ExecutionError) as caught,
    ):
        asyncio.run(workflow())
    assert caught.value.__cause__ is stop
    assert requests == 3 and observed_usage == [0, 1, 2, 3]
    assert [operation.status for operation in operations] == ["rejected"] * 3
    assert len({operation.request.operation_id for operation in operations}) == 3


def test_cancellation_while_waiting_for_correction_prevents_another_model_request() -> None:
    requests = 0
    terminals: list[StepFinished] = []
    effects: list[str] = []

    async def execute() -> None:
        waiting = asyncio.Event()

        async def before_request(context: Any, request_context: Any) -> Any:
            if context.usage.requests:
                waiting.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    # Pydantic AI owns an AnyIO cancellation scope; the host shields its asynchronous cleanup.
                    with anyio.CancelScope(shield=True):
                        await asyncio.sleep(0)
                        effects.append("request cleanup")
            return request_context

        async def respond(messages: Any, information: Any) -> ModelResponse:
            nonlocal requests
            requests += 1
            return ModelResponse(parts=[ToolCallPart("nh_evaluate", {"expression": "7"}, "refused")])

        @nh.function
        async def workflow() -> None:
            """execute
            Evaluate a permitted expression.
            """

        executor = nh.AgentStepExecutor(configuration=nh.StepExecutorConfiguration(execution_model=FunctionModel(respond)))
        with (
            nh.run(executor),
            nh.scope(
                capabilities=[Hooks(before_model_request=before_request)],
                oversight=Oversight(inspect_operation=lambda request: RejectOperation("Unavailable")),
                lifecycle=StepLifecycle(on_step_finished=terminals.append),
            ),
        ):
            task = asyncio.create_task(workflow())
            await waiting.wait()
            task.cancel("Host interruption")
            with pytest.raises(asyncio.CancelledError, match="Host interruption"):
                await task

    asyncio.run(execute())
    assert requests == 1 and effects == ["request cleanup"]
    assert len(terminals) == 1 and terminals[0].kind == "interrupted"


@pytest.mark.parametrize("catch_in_python", [False, True])
def test_child_abort_stays_terminal_while_python_controls_parent_recovery(catch_in_python: bool) -> None:
    terminals: list[StepFinished] = []
    operations: list[OperationFinished] = []

    @nh.function
    async def child() -> None:
        """execute
        Child execution.
        """

    async def invoke_child() -> int:
        try:
            await child()
        except StepAbortedError:
            if not catch_in_python:
                raise
        return 7

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            if "Child execution" in request.processed_natural_program:
                with pytest.raises(StepAbortedError):
                    await evaluate_expression_async(request.step_context, "forbidden")
                # Suppression inside the child's executor cannot make the child succeed.
                return StepCandidate(outcome=Pass())
            await evaluate_expression_async(request.step_context, "invoke_child()")
            return StepCandidate(outcome=Pass())

    @nh.function
    async def parent() -> None:
        """execute
        Parent execution.
        """

    def inspect(request: OperationRequest) -> Accept | AbortStep:
        return AbortStep("End the child step") if request.expression == "forbidden" else Accept()

    with (
        nh.run(Executor()),
        nh.scope(
            task_references={"invoke_child": invoke_child},
            oversight=Oversight(inspect_operation=inspect),
            lifecycle=StepLifecycle(on_step_finished=terminals.append, on_operation_finished=operations.append),
        ),
    ):
        if catch_in_python:
            asyncio.run(parent())
        else:
            with pytest.raises(StepAbortedError):
                asyncio.run(parent())
    assert len(terminals) == 2
    assert terminals[0].kind == "failed"
    assert terminals[1].kind == ("completed" if catch_in_python else "failed")
    assert terminals[0].execution_reference.parent_step_execution_id == terminals[1].execution_reference.step_execution_id
    assert [operation.status for operation in operations] == ["aborted", "succeeded" if catch_in_python else "failed"]
    if not catch_in_python:
        assert isinstance(terminals[1], nh.lifecycle.StepFailed)
        assert terminals[1].failure_stage == "operation_execution"
        assert terminals[1].inspection_subject is None


@pytest.mark.parametrize("output_retries", [0, 1])
def test_native_output_refusal_skips_coemitted_tools_and_respects_retry_limit(output_retries: int) -> None:
    operations: list[OperationFinished] = []
    requests = 0

    def forbidden() -> int:
        raise AssertionError("A refused expression cannot run")

    def later(value: int) -> None:
        raise AssertionError("A skipped sibling cannot run")

    async def validate(context: Any, value: int) -> None:
        raise AssertionError("A skipped sibling cannot be validated")

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        if requests == 1:
            return ModelResponse(
                parts=[
                    TextPart(json.dumps({"result": {"kind": "return", "return_expression": "forbidden()"}})),
                    ToolCallPart("later", {"value": 1}, "later"),
                ]
            )
        feedback = [part.content for message in messages for part in message.parts if isinstance(part, (RetryPromptPart, ToolReturnPart))]
        assert "Host refusal" in str(feedback) and operations[0].request.operation_id in str(feedback)
        assert "skipped" in str(feedback)
        return ModelResponse(parts=[TextPart(json.dumps({"result": {"kind": "return", "return_expression": "7"}}))])

    @nh.function
    async def workflow() -> int:
        """execute
        Return a permitted value.
        """
        return 0

    profile = ModelProfile(supports_json_schema_output=True, default_structured_output_mode="native")
    agent = Agent(FunctionModel(respond, profile=profile), deps_type=nh.StepContext, end_strategy="early", retries={"output": output_retries})
    with (
        nh.run(nh.AgentStepExecutor.from_agent(agent=agent)),
        nh.scope(
            tools=[Tool(later, args_validator=validate)],
            task_references={"forbidden": forbidden},
            oversight=Oversight(
                inspect_operation=lambda request: RejectOperation("Host refusal") if request.expression == "forbidden()" else Accept()
            ),
            lifecycle=StepLifecycle(on_operation_finished=operations.append),
        ),
    ):
        if output_retries:
            assert asyncio.run(workflow()) == 7
        else:
            with pytest.raises(nh.ExecutionError, match="Exceeded maximum output retries"):
                asyncio.run(workflow())
    assert requests == (2 if output_retries else 1)
    assert [operation.status for operation in operations] == (["rejected", "skipped", "succeeded"] if output_retries else ["rejected"])


def test_accepted_output_cannot_hide_exhausted_rejection_retries() -> None:
    requests = 0
    operations: list[OperationFinished] = []

    async def respond(messages: Any, information: Any) -> ModelResponse:
        nonlocal requests
        requests += 1
        return ModelResponse(
            parts=[
                ToolCallPart(information.output_tools[0].name, {"result": {"kind": "return", "return_expression": expression}}, str(i))
                for i, expression in enumerate(("7", "forbidden()"))
            ]
        )

    @nh.function
    async def workflow() -> int:
        """execute
        Return a permitted value.
        """
        return 0

    agent = Agent(FunctionModel(respond), deps_type=nh.StepContext, end_strategy="exhaustive", retries={"output": 0})
    with (
        nh.run(nh.AgentStepExecutor.from_agent(agent=agent)),
        nh.scope(
            oversight=Oversight(
                inspect_operation=lambda request: RejectOperation("Unavailable") if request.expression == "forbidden()" else Accept()
            ),
            lifecycle=StepLifecycle(on_operation_finished=operations.append),
        ),
        pytest.raises(nh.ExecutionError, match="Exceeded maximum output retries"),
    ):
        asyncio.run(workflow())
    assert requests == 1
    assert [operation.status for operation in operations] == ["succeeded", "rejected"]


def test_corrected_operation_does_not_bypass_commit_abort() -> None:
    terminals: list[StepFinished] = []
    operations: list[OperationFinished] = []

    class Executor:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            with pytest.raises(OperationRejectedError):
                await evaluate_expression_async(request.step_context, "forbidden()")
            assert await evaluate_expression_async(request.step_context, "7") == 7
            return StepCandidate(outcome=Pass())

    @nh.function
    async def workflow() -> None:
        """execute
        Complete a permitted operation.
        """

    with (
        nh.run(Executor()),
        nh.scope(
            oversight=Oversight(
                inspect_operation=lambda request: RejectOperation("Unavailable") if request.expression == "forbidden()" else Accept(),
                inspect_step_commit=lambda commit: AbortStep("The final result is unacceptable"),
            ),
            lifecycle=StepLifecycle(on_operation_finished=operations.append, on_step_finished=terminals.append),
        ),
        pytest.raises(StepAbortedError, match="The final result is unacceptable"),
    ):
        asyncio.run(workflow())
    assert [operation.status for operation in operations] == ["rejected", "succeeded"]
    assert len(terminals) == 1 and isinstance(terminals[0], nh.lifecycle.StepFailed)
    assert terminals[0].failure_stage == "oversight_abort" and terminals[0].inspection_subject == "step_commit"
