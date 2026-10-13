import asyncio

import pytest

import nighthawk as nh
from nighthawk.errors import NighthawkError
from nighthawk.runtime.scoping import _current_task_references
from nighthawk.runtime.step_executor import AgentStepExecutor
from tests.support.executors import StubExecutor


@pytest.mark.parametrize("entry", ["run", "scope"])
@pytest.mark.parametrize("invalid", [None, object(), 7])
def test_invalid_executor_leaves_parent_context_intact(entry: str, invalid: object) -> None:
    executor = StubExecutor()
    with nh.run(executor), nh.scope(task_references={"marker": executor}):
        reference = nh.get_execution_reference()
        meter = nh.get_usage_meter()
        with pytest.raises(TypeError, match="step executor protocol"), getattr(nh, entry)(step_executor=invalid):
            pytest.fail("Invalid executor entered its context")
        assert nh.get_step_executor() is executor
        assert nh.get_execution_reference() is reference
        assert nh.get_usage_meter() is meter
        assert _current_task_references() == {"marker": executor}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_run_and_scope_accept_each_executor_protocol(asynchronous: bool) -> None:
    class Synchronous:
        def run_step(self, *, request: nh.runtime.StepRequest) -> nh.runtime.StepCandidate:
            return nh.runtime.StepCandidate(outcome=nh.runtime.Pass())

    class Asynchronous:
        async def run_step_async(self, *, request: nh.runtime.StepRequest) -> nh.runtime.StepCandidate:
            return nh.runtime.StepCandidate(outcome=nh.runtime.Pass())

    executor = Asynchronous() if asynchronous else Synchronous()
    with nh.run(executor):
        assert nh.get_step_executor() is executor
        with nh.scope(step_executor=executor):
            assert nh.get_step_executor() is executor


def test_step_executor_replace_and_getter():
    step_executor = StubExecutor()

    with nh.run(
        step_executor,
    ):
        step_executor_value = nh.get_step_executor()
        assert step_executor_value == step_executor

    with pytest.raises(NighthawkError):
        nh.get_step_executor()


def test_scope_configuration_replaces_executor_configuration():
    class FakeAgent:
        def run_sync(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            _ = args
            _ = kwargs
            raise AssertionError

    configuration_1 = nh.StepExecutorConfiguration()
    configuration_2 = nh.StepExecutorConfiguration(user_prompt_suffix_fragments=("child",))

    with nh.run(
        nh.AgentStepExecutor.from_agent(
            agent=FakeAgent(),
            configuration=configuration_1,
        )
    ):
        initial_step_executor = nh.get_step_executor()
        assert isinstance(initial_step_executor, AgentStepExecutor)
        assert initial_step_executor.configuration == configuration_1

        with nh.scope(
            step_executor_configuration=configuration_2,
        ):
            scoped_step_executor = nh.get_step_executor()
            assert isinstance(scoped_step_executor, AgentStepExecutor)
            assert scoped_step_executor.configuration == configuration_2

        restored_step_executor = nh.get_step_executor()
        assert isinstance(restored_step_executor, AgentStepExecutor)
        assert restored_step_executor.configuration == configuration_1


def test_scope_keeps_run_id_and_generates_new_scope_id() -> None:
    with nh.run(
        StubExecutor(),
        run_id="run-test",
    ):
        parent_execution_reference = nh.get_execution_reference()
        with nh.scope():
            nested_execution_reference = nh.get_execution_reference()

        assert parent_execution_reference.run_id == "run-test"
        assert nested_execution_reference.run_id == "run-test"
        assert parent_execution_reference.scope_id != nested_execution_reference.scope_id


def test_scope_requires_existing_step_executor():
    with pytest.raises(NighthawkError), nh.scope():
        pass


def test_scope_merge_combines_task_references_and_restores_parent() -> None:
    parent_function = object()
    child_function = object()

    with nh.run(StubExecutor()):
        assert _current_task_references() == {}

        with nh.scope(task_references={"parent": parent_function}):
            assert _current_task_references() == {"parent": parent_function}

            with nh.scope(task_references=nh.Merge({"child": child_function, "parent": parent_function})):
                assert _current_task_references() == {"parent": parent_function, "child": child_function}

            assert _current_task_references() == {"parent": parent_function}

        assert _current_task_references() == {}


def test_scope_merge_rejects_conflicting_task_references() -> None:
    with (
        nh.run(StubExecutor()),
        nh.scope(task_references={"shared": object()}),
        pytest.raises(
            NighthawkError,
            match="Conflict for task reference",
        ),
        nh.scope(task_references=nh.Merge({"shared": object()})),
    ):
        pass


def test_scope_task_references_accepts_mapping() -> None:
    with nh.run(StubExecutor()), nh.scope(task_references={"one": object(), "two": object()}):
        assert set(_current_task_references().keys()) == {"one", "two"}


def test_scope_unset_inherits_task_references() -> None:
    parent_function = object()

    with nh.run(StubExecutor()), nh.scope(task_references={"parent": parent_function}), nh.scope(task_references=nh.UNSET):
        assert _current_task_references() == {"parent": parent_function}


def test_scope_empty_mapping_clears_task_references() -> None:
    parent_function = object()

    with nh.run(StubExecutor()), nh.scope(task_references={"parent": parent_function}), nh.scope(task_references={}):
        assert _current_task_references() == {}


def test_scope_unset_inherits_prompt_suffix_fragments() -> None:
    with nh.run(StubExecutor()), nh.scope(system_prompt_suffix_fragments=["parent"]), nh.scope(system_prompt_suffix_fragments=nh.UNSET):
        assert nh.get_system_prompt_suffix_fragments() == ("parent",)


def test_scope_empty_sequence_clears_prompt_suffix_fragments() -> None:
    with nh.run(StubExecutor()), nh.scope(system_prompt_suffix_fragments=["parent"]), nh.scope(system_prompt_suffix_fragments=[]):
        assert nh.get_system_prompt_suffix_fragments() == ()


def test_scope_sequence_replaces_prompt_suffix_fragments() -> None:
    with (
        nh.run(StubExecutor()),
        nh.scope(system_prompt_suffix_fragments=["parent"]),
        nh.scope(system_prompt_suffix_fragments=["child_1", "child_2"]),
    ):
        assert nh.get_system_prompt_suffix_fragments() == ("child_1", "child_2")


def test_prompt_suffix_fragments_default_to_empty() -> None:
    with nh.run(StubExecutor()):
        assert nh.get_system_prompt_suffix_fragments() == ()
        assert nh.get_user_prompt_suffix_fragments() == ()


def test_ambient_getters_outside_run_raise() -> None:
    with pytest.raises(NighthawkError):
        nh.get_system_prompt_suffix_fragments()
    with pytest.raises(NighthawkError):
        nh.get_user_prompt_suffix_fragments()


def test_scope_mapping_replaces_task_references_and_restores_parent() -> None:
    parent_function = object()
    replacement_function = object()

    with nh.run(StubExecutor()), nh.scope(task_references={"parent": parent_function}):
        assert _current_task_references() == {"parent": parent_function}

        with nh.scope(task_references={"replacement": replacement_function}):
            assert _current_task_references() == {"replacement": replacement_function}

        assert _current_task_references() == {"parent": parent_function}


def test_scope_unset_inherits_step_executor() -> None:
    with nh.run(StubExecutor()):
        parent_step_executor = nh.get_step_executor()
        with nh.scope(step_executor=nh.UNSET):
            child_step_executor = nh.get_step_executor()
        assert child_step_executor is parent_step_executor


def test_scope_extend_appends_prompt_suffix_fragments() -> None:
    with (
        nh.run(StubExecutor()),
        nh.scope(system_prompt_suffix_fragments=["parent"]),
        nh.scope(system_prompt_suffix_fragments=nh.Extend(["child"])),
    ):
        assert nh.get_system_prompt_suffix_fragments() == ("parent", "child")


def test_decorated_function_requires_step_executor():
    @nh.natural_function
    def f(x: int):
        f"""natural
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """  # noqa: B021
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        f(1)


def test_async_decorated_function_requires_step_executor():
    @nh.natural_function
    async def f(x: int):
        f"""natural
        <:result>
        {{"step_outcome": {{"kind": "pass"}}, "bindings": {{"result": {x + 1}}}}}
        """  # noqa: B021
        return result  # type: ignore # noqa: F821

    with pytest.raises(NighthawkError):
        asyncio.run(f(1))


def test_host_integration_defaults() -> None:
    with nh.run(StubExecutor()):
        assert nh.get_tools() == ()
        assert nh.get_capabilities() == ()
        assert nh.get_oversight() is None
