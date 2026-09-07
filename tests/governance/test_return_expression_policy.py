from __future__ import annotations

import ast
import asyncio
import inspect
from collections.abc import Generator
from typing import Annotated

import pytest
from pydantic import AfterValidator

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFinished, StepLifecycle
from nighthawk.oversight import Accept, Oversight, Reject, ReturnExpression
from nighthawk.runtime.step_contract import ReturnStepOutcome
from nighthawk.testing import ScriptedExecutor, StepResponse


def approve_validated_binding(request: ReturnExpression) -> Accept | Reject:
    """Example host policy; return validation and commit approval remain necessary."""
    try:
        expression = ast.parse(request.expression, mode="eval").body
    except SyntaxError:
        return Reject("Invalid return expression")
    if not isinstance(expression, ast.Name) or expression.id not in request.validated_binding_name_to_value:
        return Reject("Return must name a validated write from this candidate")
    if inspect.isawaitable(request.validated_binding_name_to_value[expression.id]):
        return Reject("Awaitable writes are not permitted as returns")
    return Accept()


@pytest.mark.parametrize(
    "expression,accepted",
    [
        ("value", True),
        ("(value)", True),
        ("previous", False),
        ("value.real", False),
        ("value + 0", False),
        ("calculate()", False),
        ("99", False),
        ("None", False),
        ("[value]", False),
        ("(", False),
    ],
)
def test_host_name_policy_requires_a_current_validated_write(expression: str, accepted: bool) -> None:
    @nh.natural_function
    def workflow() -> int:
        value: int = 0
        previous: int = 7
        """natural
        <:value> <:previous>
        Finish.
        """
        return value + previous

    executor = ScriptedExecutor([StepResponse(bindings={"value": 4}, outcome=ReturnStepOutcome(kind="return", return_expression=expression))])
    with nh.run(executor), nh.scope(oversight=Oversight(inspect_return_expression=approve_validated_binding)):
        if accepted:
            assert workflow() == 4
        else:
            with pytest.raises(nh.ExecutionError) as caught:
                workflow()
            assert caught.value.step_failed is not None
            assert caught.value.step_failed.failure_stage == "oversight_rejection"
            assert caught.value.step_failed.inspection_subject == "return_expression"
            assert not caught.value.step_failed.assigned_binding_name_to_value


def test_name_only_policy_does_not_skip_return_validation() -> None:
    validations: list[int] = []
    events: list[StepFinished] = []

    def increment(value: int) -> int:
        validations.append(value)
        return value + 1

    Counted = Annotated[int, AfterValidator(increment)]  # noqa: N806

    @nh.natural_function
    def workflow() -> Counted:  # pyright: ignore[reportInvalidTypeForm]
        value: Counted = 0  # pyright: ignore[reportInvalidTypeForm]
        """natural
        <Counted> <:value>
        Finish.
        """
        return value

    executor = ScriptedExecutor([StepResponse(bindings={"value": 1}, outcome=ReturnStepOutcome(kind="return", return_expression="value"))])
    with (
        nh.run(executor),
        nh.scope(
            oversight=Oversight(inspect_return_expression=approve_validated_binding),
            lifecycle=StepLifecycle(events.append),
        ),
    ):
        assert workflow() == 3
    assert validations == [1, 2]  # One binding validation, then one independent return validation.
    assert len(events) == 1
    event = events[0]
    assert isinstance(event, StepCompleted) and isinstance(event.outcome, nh.oversight.Return)
    assert event.assigned_binding_name_to_value == {"value": 2}
    assert event.outcome.value == 3


@pytest.mark.parametrize("reject_awaitable", [False, True])
def test_a_bare_name_can_still_trigger_await(reject_awaitable: bool) -> None:
    awaited: list[str] = []

    class Pending:
        def __await__(self) -> Generator[None, None, int]:
            awaited.append("awaited")
            yield
            return 9

    @nh.natural_function
    async def workflow() -> int:
        value: object = None  # noqa: F841
        """natural
        <:value>
        Finish.
        """
        return 0

    def inspect_expression(request: ReturnExpression) -> Accept | Reject:
        if reject_awaitable:
            return approve_validated_binding(request)
        expression = ast.parse(request.expression, mode="eval").body
        assert isinstance(expression, ast.Name) and expression.id in request.validated_binding_name_to_value
        return Accept()

    executor = ScriptedExecutor([StepResponse(bindings={"value": Pending()}, outcome=ReturnStepOutcome(kind="return", return_expression="value"))])
    with nh.run(executor), nh.scope(oversight=Oversight(inspect_return_expression=inspect_expression)):
        if reject_awaitable:
            with pytest.raises(nh.ExecutionError) as caught:
                asyncio.run(workflow())
            assert caught.value.step_failed is not None
            assert caught.value.step_failed.failure_stage == "oversight_rejection"
        else:
            assert asyncio.run(workflow()) == 9
    assert awaited == ([] if reject_awaitable else ["awaited"])


def test_binding_rewrite_can_diverge_from_an_approved_name_return() -> None:
    events: list[StepFinished] = []

    @nh.natural_function
    def workflow() -> int:
        value: int = 0
        """natural
        <:value>
        Finish.
        """
        return value

    executor = ScriptedExecutor([StepResponse(bindings={"value": 4}, outcome=ReturnStepOutcome(kind="return", return_expression="value"))])
    with (
        nh.run(executor),
        nh.scope(
            lifecycle=StepLifecycle(events.append),
            oversight=Oversight(
                inspect_return_expression=approve_validated_binding,
                inspect_step_commit=lambda commit: nh.oversight.Rewrite(binding_name_to_value={"value": 9}),
            ),
        ),
    ):
        assert workflow() == 4
    event = events[0]
    assert isinstance(event, StepCompleted) and isinstance(event.outcome, nh.oversight.Return)
    assert event.outcome.value == 4
    assert event.assigned_binding_name_to_value == {"value": 9}
