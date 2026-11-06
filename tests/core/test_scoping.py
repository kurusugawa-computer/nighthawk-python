import asyncio

import pytest

import nighthawk as nh
from nighthawk.adapters.pydantic_ai.configuration import StepExecutorConfiguration
from nighthawk.adapters.pydantic_ai.executor import AgentStepExecutor
from nighthawk.adapters.pydantic_ai.scoping import get_model_scope
from nighthawk.core import lifecycle as lifecycle_contract
from nighthawk.core.scoping import get_scope
from nighthawk.core.step_outcome import Pass
from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.errors import NighthawkError
from tests.support.executors import StubExecutor


@pytest.mark.parametrize("entry", ["run", "scope"])
@pytest.mark.parametrize("invalid", [None, object(), 7])
def test_invalid_executor_leaves_parent_context_intact(entry: str, invalid: object) -> None:
    executor = StubExecutor()
    with nh.run(executor), nh.scope(task_references={"marker": executor}):
        reference = get_scope().execution_reference
        meter = get_scope().usage_meter
        with pytest.raises(TypeError, match="step executor protocol"), getattr(nh, entry)(step_executor=invalid):
            pytest.fail("Invalid executor entered its context")
        assert get_scope().step_executor is executor
        assert get_scope().execution_reference is reference
        assert get_scope().usage_meter is meter
        assert get_scope().task_references == {"marker": executor}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_run_and_scope_accept_each_executor_protocol(asynchronous: bool) -> None:
    class Synchronous:
        def run_step(self, *, request: StepRequest) -> StepCandidate:
            return StepCandidate(outcome=Pass())

    class Asynchronous:
        async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
            return StepCandidate(outcome=Pass())

    executor = Asynchronous() if asynchronous else Synchronous()
    with nh.run(executor):
        assert get_scope().step_executor is executor
        with nh.scope(step_executor=executor):
            assert get_scope().step_executor is executor


def test_run_and_scope_yield_the_active_frozen_scope() -> None:
    executor = StubExecutor()
    lifecycle = lifecycle_contract.StepLifecycle()

    def helper() -> None: ...

    with nh.run(executor, run_id="run-test") as run_scope:
        assert get_scope() is run_scope
        assert run_scope.execution_reference.run_id == "run-test"
        assert run_scope.step_executor is executor
        assert run_scope.oversight is None
        assert run_scope.lifecycle is None
        assert get_model_scope().tools == ()
        assert get_model_scope().system_prompt_suffix_fragments == ()
        assert get_model_scope().user_prompt_suffix_fragments == ()
        with nh.scope(
            lifecycle=lifecycle,
            task_references={"helper": helper},
            tools=[helper],
            user_prompt_suffix_fragments=["Be brief."],
        ) as child_scope:
            assert get_scope() is child_scope
            assert child_scope.execution_reference.run_id == run_scope.execution_reference.run_id
            assert child_scope.execution_reference.scope_id != run_scope.execution_reference.scope_id
            assert child_scope.lifecycle is lifecycle
            assert child_scope.task_references["helper"] is helper
            assert [tool.name for tool in get_model_scope().tools] == ["helper"]
            assert get_model_scope().user_prompt_suffix_fragments == ("Be brief.",)
            assert child_scope.usage_meter is run_scope.usage_meter
            with pytest.raises(TypeError):
                child_scope.task_references["other"] = helper  # type: ignore[index]
        assert get_scope() is run_scope
    with pytest.raises(NighthawkError):
        get_scope()


def test_scope_configuration_replaces_executor_configuration():
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    configuration_1 = StepExecutorConfiguration()
    configuration_2 = StepExecutorConfiguration(user_prompt_suffix_fragments=("child",))

    with nh.run(
        AgentStepExecutor.from_agent(
            agent=FakeAgent(),
            configuration=configuration_1,
        )
    ):
        initial_step_executor = get_scope().step_executor
        assert isinstance(initial_step_executor, AgentStepExecutor)
        assert initial_step_executor.configuration == configuration_1

        with nh.scope(
            step_executor_configuration=configuration_2,
        ):
            scoped_step_executor = get_scope().step_executor
            assert isinstance(scoped_step_executor, AgentStepExecutor)
            assert scoped_step_executor.configuration == configuration_2

        restored_step_executor = get_scope().step_executor
        assert isinstance(restored_step_executor, AgentStepExecutor)
        assert restored_step_executor.configuration == configuration_1


def test_scope_requires_existing_step_executor():
    with pytest.raises(NighthawkError), nh.scope():
        pass


def test_scope_merge_combines_task_references_and_restores_parent() -> None:
    parent_function = object()
    child_function = object()

    with nh.run(StubExecutor()):
        assert get_scope().task_references == {}

        with nh.scope(task_references={"parent": parent_function}):
            assert get_scope().task_references == {"parent": parent_function}

            with nh.scope(task_references=nh.Merge({"child": child_function, "parent": parent_function})):
                assert get_scope().task_references == {"parent": parent_function, "child": child_function}

            assert get_scope().task_references == {"parent": parent_function}

        assert get_scope().task_references == {}


def test_scope_unset_inherits_task_references() -> None:
    parent_function = object()

    with nh.run(StubExecutor()), nh.scope(task_references={"parent": parent_function}), nh.scope(task_references=nh.UNSET):
        assert get_scope().task_references == {"parent": parent_function}


def test_scope_unset_inherits_prompt_suffix_fragments() -> None:
    with nh.run(StubExecutor()), nh.scope(system_prompt_suffix_fragments=["parent"]), nh.scope(system_prompt_suffix_fragments=nh.UNSET):
        assert get_model_scope().system_prompt_suffix_fragments == ("parent",)


def test_scope_sequence_replaces_prompt_suffix_fragments() -> None:
    with (
        nh.run(StubExecutor()),
        nh.scope(system_prompt_suffix_fragments=["parent"]),
        nh.scope(system_prompt_suffix_fragments=["child_1", "child_2"]),
    ):
        assert get_model_scope().system_prompt_suffix_fragments == ("child_1", "child_2")


def test_scope_mapping_replaces_task_references_and_restores_parent() -> None:
    parent_function = object()
    replacement_function = object()

    with nh.run(StubExecutor()), nh.scope(task_references={"parent": parent_function}):
        assert get_scope().task_references == {"parent": parent_function}

        with nh.scope(task_references={"replacement": replacement_function}):
            assert get_scope().task_references == {"replacement": replacement_function}

        assert get_scope().task_references == {"parent": parent_function}


def test_scope_unset_inherits_step_executor() -> None:
    with nh.run(StubExecutor()):
        parent_step_executor = get_scope().step_executor
        with nh.scope(step_executor=nh.UNSET):
            child_step_executor = get_scope().step_executor
        assert child_step_executor is parent_step_executor


def test_decorated_function_requires_step_executor():
    @nh.function
    def f(x: int):
        f"""execute
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """  # noqa: B021
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        f(1)


def test_async_decorated_function_requires_step_executor():
    @nh.function
    async def f(x: int):
        f"""execute
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """  # noqa: B021
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        asyncio.run(f(1))


@pytest.mark.parametrize("invalid", [None, "", {}, False])
def test_run_identifier_rejection_preserves_parent(invalid: object) -> None:
    with nh.run(StubExecutor(), run_id="parent"):
        reference = get_scope().execution_reference
        with pytest.raises((TypeError, ValueError)), nh.run(StubExecutor(), run_id=invalid):  # type: ignore[arg-type]
            pytest.fail("Invalid run identifier installed context")
        assert get_scope().execution_reference is reference


def test_run_omission_creates_identity_and_meter_while_scope_inherits_meter() -> None:
    with nh.run(StubExecutor()):
        reference = get_scope().execution_reference
        meter = get_scope().usage_meter
        with nh.scope(usage_meter=nh.UNSET):
            assert get_scope().usage_meter is meter
        with nh.run(StubExecutor(), run_id=nh.UNSET, usage_meter=nh.UNSET):
            assert get_scope().execution_reference.run_id != reference.run_id
            assert get_scope().usage_meter is not meter
        assert get_scope().usage_meter is meter
