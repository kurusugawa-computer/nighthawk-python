from __future__ import annotations

import asyncio
from typing import Annotated, TypedDict, Unpack

import pytest
from pydantic import Field

import nighthawk as nh
from nighthawk.runtime import Pass, StepCandidate, StepRequest, assign_tool
from nighthawk.runtime.operation_contract import OperationError


class ReviewOptions(TypedDict):
    attempts: int
    label: str


class CandidateExecutor:
    def __init__(self, value: object, *, use_assignment: bool = False) -> None:
        self.value = value
        self.use_assignment = use_assignment

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        if self.use_assignment:
            assign_tool(request.step_context, "values", repr(self.value))
            return StepCandidate(outcome=Pass())
        return StepCandidate(outcome=Pass(), binding_name_to_value={"values": self.value})


@pytest.mark.parametrize("use_assignment", [False, True])
def test_positional_variadic_output_validates_elements_and_retains_tuple(use_assignment: bool) -> None:
    @nh.natural_function
    def workflow(*values: Annotated[int, Field(ge=0)]) -> tuple[int, ...]:
        """natural
        Update <:values>.
        """
        return values

    with nh.run(CandidateExecutor(["2", 3], use_assignment=use_assignment)):
        assert workflow(1) == (2, 3)
    with (
        nh.run(CandidateExecutor([-1], use_assignment=use_assignment)),
        pytest.raises(nh.ExecutionError, match="greater than or equal to 0") as caught,
    ):
        workflow(1)
    if use_assignment:
        assert isinstance(caught.value.__cause__, OperationError)


@pytest.mark.parametrize("use_assignment", [False, True])
def test_keyword_variadic_output_validates_values(use_assignment: bool) -> None:
    @nh.natural_function
    async def workflow(**values: Annotated[int, Field(ge=0)]) -> dict[str, int]:
        """natural
        Update <:values>.
        """
        return values

    with nh.run(CandidateExecutor({"attempts": "2"}, use_assignment=use_assignment)):
        assert asyncio.run(workflow(attempts=1)) == {"attempts": 2}
    with (
        nh.run(CandidateExecutor({"attempts": -1}, use_assignment=use_assignment)),
        pytest.raises(nh.ExecutionError, match="greater than or equal to 0") as caught,
    ):
        asyncio.run(workflow(attempts=1))
    if use_assignment:
        assert isinstance(caught.value.__cause__, OperationError)


def test_unpacked_positional_annotation_is_already_a_collection_contract() -> None:
    @nh.natural_function
    def workflow(*values: Unpack[tuple[int, str]]) -> tuple[int, str]:  # noqa: UP044
        """natural
        Update <:values>.
        """
        return values

    with nh.run(CandidateExecutor(["2", "review"])):
        assert workflow(1, "initial") == (2, "review")


def test_starred_positional_annotation_is_already_a_collection_contract() -> None:
    @nh.natural_function
    def workflow(*values: *tuple[int, str]) -> tuple[int, str]:
        """natural
        Update <:values>.
        """
        return values

    with nh.run(CandidateExecutor(["2", "review"])):
        assert workflow(1, "initial") == (2, "review")


def test_unpacked_keyword_annotation_preserves_typed_dictionary_contract() -> None:
    @nh.natural_function
    def workflow(**values: Unpack[ReviewOptions]) -> ReviewOptions:
        """natural
        Update <:values>.
        """
        return values

    with nh.run(CandidateExecutor({"attempts": "2", "label": "review"})):
        assert workflow(attempts=1, label="initial") == {"attempts": 2, "label": "review"}
    with nh.run(CandidateExecutor({"attempts": 2})), pytest.raises(nh.ExecutionError, match="Field required"):
        workflow(attempts=1, label="initial")


def test_variadic_contracts_do_not_resolve_shadowed_container_names() -> None:
    @nh.natural_function
    def workflow(tuple: object, dict: object, str: object, *values: int) -> object:
        """natural
        Update <:values>.
        """
        return values

    with nh.run(CandidateExecutor(["2"])):
        assert workflow(None, None, None, 1) == (2,)
