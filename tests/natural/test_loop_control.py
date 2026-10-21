from collections.abc import AsyncIterator

import pytest

import nighthawk as nh
from nighthawk.runtime import Break, Continue
from nighthawk.testing import ScriptedExecutor, StepResponse


@pytest.mark.parametrize("loop_kind", ["for", "while"])
def test_loop_else_has_no_loop_outcomes(loop_kind: str) -> None:
    @nh.function
    def workflow() -> int:
        result: int = 0
        if loop_kind == "for":
            for number in ():
                result += number
            else:
                """execute
                Set <:result>.
                """
        else:
            while False:
                pass
            else:
                """execute
                Set <:result>.
                """
        return result

    executor = ScriptedExecutor([StepResponse(binding_name_to_value={"result": 7})])
    with nh.run(executor):
        assert workflow() == 7
    assert set(executor.calls[0].allowed_outcome_kinds) == {"pass", "return", "raise"}


async def empty_values() -> AsyncIterator[int]:
    for value in ():
        yield value


@pytest.mark.asyncio
async def test_async_loop_else_has_no_loop_outcomes() -> None:
    @nh.function
    async def workflow() -> int:
        result: int = 0
        async for number in empty_values():
            result += number
        else:
            """execute
            Set <:result>.
            """
        return result

    executor = ScriptedExecutor([StepResponse(binding_name_to_value={"result": 9})])
    with nh.run(executor):
        assert await workflow() == 9
    assert set(executor.calls[0].allowed_outcome_kinds) == {"pass", "return", "raise"}


@pytest.mark.parametrize("outcome", [Break(), Continue()])
def test_inner_loop_else_controls_the_enclosing_loop(outcome: Break | Continue) -> None:
    @nh.function
    def workflow() -> list[str]:
        effects: list[str] = []
        for _number in range(2):
            for _ in ():
                pass
            else:
                """execute
                Choose a loop outcome.
                """
            effects.append("after inner loop")
        else:
            effects.append("outer loop exhausted")
        return effects

    executor = ScriptedExecutor(default_response=StepResponse(outcome=outcome))
    with nh.run(executor):
        assert workflow() == ([] if isinstance(outcome, Break) else ["outer loop exhausted"])
    assert len(executor.calls) == (1 if isinstance(outcome, Break) else 2)
    assert all({"break", "continue"} <= set(call.allowed_outcome_kinds) for call in executor.calls)
