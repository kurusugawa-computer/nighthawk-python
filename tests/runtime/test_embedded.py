from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import pytest
from pydantic import AfterValidator, BaseModel, ValidationError

import nighthawk as nh
from nighthawk.lifecycle import StepCompleted, StepFailed, StepFinished, StepInterrupted, StepLifecycle, StepRaised
from nighthawk.oversight import Accept, Oversight, Reject, Rewrite
from nighthawk.runtime import Pass, Raise, Return, StepCandidate, StepRequest, eval_expression, execute_step, execute_step_async
from nighthawk.runtime.async_bridge import run_coroutine_synchronously
from nighthawk.runtime.step_result import StepResult
from nighthawk.testing import ScriptedExecutor, StepResponse, pass_response


@dataclass
class InspectingExecutor:
    handler: Callable[[StepRequest], StepCandidate]

    def run_step(self, *, request: StepRequest) -> StepCandidate:
        return self.handler(request)

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        return self.handler(request)


@pytest.fixture(params=[False, True], ids=["synchronous", "asynchronous"])
def execute(request: pytest.FixtureRequest) -> Callable[..., StepResult]:
    if request.param:
        return lambda *arguments, **keywords: run_coroutine_synchronously(lambda: execute_step_async(*arguments, **keywords))
    return execute_step


def test_explicit_environment_is_shared_by_reads_evaluation_discovery_and_types(execute: Callable[..., StepResult]) -> None:
    value = "caller"
    CallerType = int  # noqa: N806
    shared: list[int] = []
    name_to_value: dict[str, object] = {"value": 7, "CallerType": str, "shared": shared, "result": ""}
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    def inspect(request: StepRequest) -> StepCandidate:
        environment = request.step_context.environment
        assert environment.resolve("value") == 7
        assert environment.resolve("shared") is shared
        assert request.binding_name_to_type == {"result": str}
        assert request.return_annotation == list[str]
        assert eval_expression(request.step_context, "(lambda: value)()") == 7
        assert eval_expression(request.step_context, "list(value + i for i in range(2))") == [7, 8]
        assert environment.describe("value")["origin"] == "python"
        with pytest.raises(NameError):
            environment.resolve("saved_name_to_value")
        assert "saved_name_to_value" not in request.step_context.step_globals
        return StepCandidate(outcome=Return(["done"]), binding_name_to_value={"result": "saved"})

    with nh.run(InspectingExecutor(inspect)), nh.scope(lifecycle=StepLifecycle(events.append)):
        outcome = execute(
            "Read <value> and write <:result>.",
            name_to_value=name_to_value,
            input_binding_names=("value", "shared"),
            output_binding_names=("result",),
            binding_name_to_type={"result": "CallerType"},
            return_annotation=list["CallerType"],
            write_binding=saved_name_to_value.__setitem__,
            source_location="trusted_program:12",
        )
    assert outcome == Return(["done"])
    assert value == "caller" and CallerType is int
    assert name_to_value["result"] == ""
    assert saved_name_to_value == {"result": "saved"}
    assert len(events) == 1 and isinstance(events[0], StepCompleted)
    assert events[0].assigned_binding_name_to_value == saved_name_to_value
    assert events[0].input_binding_name_to_value == {"value": 7, "shared": shared}
    assert events[0].execution_reference.source_location == "trusted_program:12"


@pytest.mark.parametrize("name", ["len", "injected", "caller_value"])
def test_missing_reads_do_not_fall_back(execute: Callable[..., StepResult], name: str) -> None:
    caller_value = 99
    executor = ScriptedExecutor([])
    events: list[StepFinished] = []
    with (
        nh.run(executor),
        nh.scope(task_references={"injected": 42}, lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError) as caught,
    ):
        execute("Read required input.", name_to_value={}, input_binding_names=(name,), write_binding=lambda name, value: None)
    assert caller_value == 99
    assert executor.calls == []
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"
    assert isinstance(events[0].original_exception, NameError)
    assert events[0].original_exception.name == name
    assert caught.value.__cause__ is events[0].original_exception


def test_empty_namespace_keeps_builtins_and_scoped_references(execute: Callable[..., StepResult]) -> None:
    def inspect(request: StepRequest) -> StepCandidate:
        assert request.step_context.step_locals == {}
        assert eval_expression(request.step_context, "len([injected])") == 1
        return StepCandidate(outcome=Pass())

    with nh.run(InspectingExecutor(inspect)), nh.scope(task_references={"injected": 42}):
        assert execute("Inspect the environment.", name_to_value={}, write_binding=lambda name, value: None) == Pass()


def test_nested_natural_uses_own_frame_and_preserves_scope(execute: Callable[..., StepResult]) -> None:
    events: list[StepFinished] = []

    @nh.natural_function
    def child(value: int) -> int:
        result = 0
        """natural
        Read <value> and write <:result>.
        """
        return result

    def inspect(request: StepRequest) -> StepCandidate:
        if request.processed_natural_program == "parent":
            assert eval_expression(request.step_context, "child(99)") == 99
            assert request.step_context.environment.resolve("value") == 7
            return StepCandidate(outcome=Pass())
        assert request.step_context.environment.resolve("value") == 99
        assert request.step_context.environment.resolve("result") == 0
        assert request.step_context.environment.resolve("shared_policy") == "parent policy"
        assert nh.get_lifecycle() is not None
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": 99})

    with nh.run(InspectingExecutor(inspect)), nh.scope(lifecycle=StepLifecycle(events.append)):
        execute(
            "parent",
            name_to_value={"value": 7, "result": "outer", "child": child, "shared_policy": "parent policy"},
            write_binding=lambda name, value: None,
        )
    assert len(events) == 2
    assert events[0].execution_reference.step_execution_id != events[1].execution_reference.step_execution_id


def test_explicit_step_cannot_inherit_parent_temporary(execute: Callable[..., StepResult]) -> None:
    def inspect(request: StepRequest) -> StepCandidate:
        request.step_context.record_assignment("parent_only", 10)
        with pytest.raises(nh.ExecutionError, match="parent_only"):
            execute("child", name_to_value={}, input_binding_names=("parent_only",), write_binding=lambda name, value: None)
        return StepCandidate(outcome=Pass())

    with nh.run(InspectingExecutor(inspect)):
        execute("parent", name_to_value={}, write_binding=lambda name, value: None)


def test_callable_keeps_its_closure(execute: Callable[..., StepResult]) -> None:
    captured = 41

    def helper() -> int:
        return captured + 1

    def inspect(request: StepRequest) -> StepCandidate:
        assert eval_expression(request.step_context, "helper()") == 42
        return StepCandidate(outcome=Pass())

    with nh.run(InspectingExecutor(inspect)):
        execute("call helper", name_to_value={"helper": helper, "captured": -1}, write_binding=lambda name, value: None)


def test_type_contracts_do_not_use_caller_types(execute: Callable[..., StepResult]) -> None:
    CallerOnlyType = int  # noqa: N806
    executor = ScriptedExecutor([])
    with nh.run(executor), pytest.raises(nh.ExecutionError, match="CallerOnlyType"):
        execute(
            "write output",
            name_to_value={},
            output_binding_names=("result",),
            binding_name_to_type={"result": "CallerOnlyType"},
            write_binding=lambda name, value: None,
        )
    assert CallerOnlyType is int
    assert executor.calls == []


def test_application_model_and_explicit_types(execute: Callable[..., StepResult]) -> None:
    class Selection(BaseModel):
        label: str

    saved_name_to_value: dict[str, object] = {}
    binding_name_to_type = {"selected": "Selection", "wide": object, "anything": Any, "empty": None}
    candidate = StepCandidate(outcome=Pass(), binding_name_to_value={"selected": {"label": "chosen"}, "wide": 7, "anything": [1], "empty": None})
    with nh.run(InspectingExecutor(lambda request: candidate)):
        execute(
            "select",
            name_to_value={"Selection": Selection, "wide": "", "anything": "", "empty": 7},
            output_binding_names=tuple(binding_name_to_type),
            binding_name_to_type=binding_name_to_type,
            write_binding=saved_name_to_value.__setitem__,
        )
    assert saved_name_to_value == {"selected": Selection(label="chosen"), "wide": 7, "anything": [1], "empty": None}


@pytest.mark.parametrize("rewrite", [False, True])
def test_initialized_output_finalization_and_empty_rewrite(execute: Callable[..., StepResult], rewrite: bool) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []
    inspections: list[object] = []

    def inspect(commit: nh.oversight.StepCommit) -> Accept | Rewrite:
        inspections.append(dict(commit.binding_name_to_value))
        return Rewrite(binding_name_to_value={}) if rewrite else Accept()

    with (
        nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass()))),
        nh.scope(oversight=Oversight(inspect_step_commit=inspect), lifecycle=StepLifecycle(events.append)),
    ):
        execute(
            "normalize",
            name_to_value={"result": " draft "},
            output_binding_names=("result", "unbound"),
            binding_name_to_type={"result": Annotated[str, AfterValidator(str.strip)]},
            write_binding=saved_name_to_value.__setitem__,
        )
    assert inspections == [{"result": "draft"}]
    assert saved_name_to_value == ({} if rewrite else {"result": "draft"})
    assert events[0].assigned_binding_name_to_value == saved_name_to_value


@pytest.mark.parametrize("reject", [False, True])
def test_validation_and_commit_rejection_never_write(execute: Callable[..., StepResult], reject: bool) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []
    with (
        nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass(), binding_name_to_value={"result": 1 if reject else "bad"}))),
        nh.scope(oversight=Oversight(inspect_step_commit=lambda commit: Reject("refused")), lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError),
    ):
        execute("write", name_to_value={"result": 0}, output_binding_names=("result",), write_binding=saved_name_to_value.__setitem__)
    assert saved_name_to_value == {}
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == ("oversight_rejection" if reject else "binding_validation")
    if not reject:
        assert isinstance(events[0].original_exception, ValidationError)


def test_partial_write_failure_records_only_completed_writes(execute: Callable[..., StepResult]) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    def write(name: str, value: object) -> None:
        if name == "second":
            raise ValueError("storage failed")
        saved_name_to_value[name] = value

    with (
        nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass(), binding_name_to_value={"second": 2, "first": 1}))),
        nh.scope(lifecycle=StepLifecycle(events.append)),
        pytest.raises(nh.ExecutionError, match="storage failed"),
    ):
        execute("write", name_to_value={}, output_binding_names=("first", "second"), write_binding=write)
    assert saved_name_to_value == {"first": 1}
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "binding_assignment"
    assert events[0].assigned_binding_name_to_value == saved_name_to_value
    assert events[0].validated_binding_name_to_value == {"first": 1, "second": 2}


def test_final_raise_discards_invalid_output_and_delivers_before_propagating(execute: Callable[..., StepResult]) -> None:
    events: list[StepFinished] = []
    saved_name_to_value: dict[str, object] = {}
    candidate = StepCandidate(outcome=Raise("domain error", error_type=LookupError), binding_name_to_value={"result": "invalid"})
    with nh.run(InspectingExecutor(lambda request: candidate)), nh.scope(lifecycle=StepLifecycle(events.append)), pytest.raises(LookupError):
        execute("raise", name_to_value={"result": 0}, output_binding_names=("result",), write_binding=saved_name_to_value.__setitem__)
    assert saved_name_to_value == {}
    assert len(events) == 1 and isinstance(events[0], StepRaised)


def test_terminal_failure_preserves_successful_write_without_redelivery(execute: Callable[..., StepResult]) -> None:
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    def finished(event: StepFinished) -> None:
        assert saved_name_to_value == {"result": 7}
        events.append(event)
        raise RuntimeError("notification failed")

    with (
        nh.run(ScriptedExecutor([pass_response(result=7)])),
        nh.scope(lifecycle=StepLifecycle(finished)),
        pytest.raises(RuntimeError, match="notification failed"),
    ):
        execute("write", name_to_value={}, output_binding_names=("result",), write_binding=saved_name_to_value.__setitem__)
    assert len(events) == 1 and isinstance(events[0], StepCompleted)
    assert events[0].assigned_binding_name_to_value == saved_name_to_value


def test_async_writer_is_awaited_even_by_sync_entry(execute: Callable[..., StepResult]) -> None:
    order: list[str] = []

    async def write(name: str, value: object) -> None:
        await asyncio.sleep(0)
        order.append(name)

    with nh.run(ScriptedExecutor([pass_response(first=1, second=2)])), nh.scope(lifecycle=StepLifecycle(lambda event: order.append("finished"))):
        execute("write", name_to_value={}, output_binding_names=("first", "second"), write_binding=write)
    assert order == ["first", "second", "finished"]


@pytest.mark.asyncio
async def test_cancellation_during_write_joins_cleanup_and_records_completed_writes() -> None:
    entered = asyncio.Event()
    cleaned_up = asyncio.Event()
    saved_name_to_value: dict[str, object] = {}
    events: list[StepFinished] = []

    async def write(name: str, value: object) -> None:
        if name == "second":
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned_up.set()
        saved_name_to_value[name] = value

    def finished(event: StepFinished) -> None:
        assert cleaned_up.is_set()
        events.append(event)

    with nh.run(ScriptedExecutor([pass_response(first=1, second=2)])), nh.scope(lifecycle=StepLifecycle(finished)):
        task = asyncio.create_task(execute_step_async("write", name_to_value={}, output_binding_names=("first", "second"), write_binding=write))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert saved_name_to_value == {"first": 1}
    assert len(events) == 1 and isinstance(events[0], StepInterrupted)
    assert events[0].failure_stage == "binding_assignment"
    assert events[0].assigned_binding_name_to_value == saved_name_to_value


def test_exception_expression_resolves_explicit_type(execute: Callable[..., StepResult]) -> None:
    from nighthawk.runtime.step_contract import RaiseStepOutcome

    response = StepResponse(outcome=RaiseStepOutcome(kind="raise", raise_message="expected", raise_error_type="SelectedError"))
    with nh.run(ScriptedExecutor([response])), pytest.raises(LookupError, match="expected"):
        execute("raise", name_to_value={"SelectedError": LookupError}, write_binding=lambda name, value: None)


def test_normal_and_embedded_execution_have_same_validation_and_lifecycle_order(execute: Callable[..., StepResult]) -> None:
    order: list[str] = []
    observed_events: list[StepFinished] = []

    def validate(value: str) -> str:
        order.append("validate")
        return value.strip()

    OutputType = Annotated[str, AfterValidator(validate)]  # noqa: N806

    @nh.natural_function
    def normal() -> str:
        nonlocal OutputType
        result: OutputType = " initial "  # type: ignore[valid-type]
        """natural
        Write <:result>.
        """
        return result

    def inspect_request(request: StepRequest) -> StepCandidate:
        order.append("execute")
        assert request.binding_name_to_type["result"] == OutputType
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": " result "})

    def inspect_commit(commit: nh.oversight.StepCommit) -> Accept:
        order.append("inspect")
        assert commit.binding_name_to_value == {"result": "result"}
        return Accept()

    def finished(event: StepFinished) -> None:
        order.append("finished")
        observed_events.append(event)

    with (
        nh.run(InspectingExecutor(inspect_request)),
        nh.scope(
            oversight=Oversight(inspect_step_commit=inspect_commit),
            lifecycle=StepLifecycle(finished, on_step_started=lambda event: order.append("started")),
        ),
    ):
        assert normal() == "result"
        normal_order = list(order)
        order.clear()
        saved_name_to_value: dict[str, object] = {}
        execute(
            "Write <:result>.",
            name_to_value={"result": " initial "},
            output_binding_names=("result",),
            binding_name_to_type={"result": OutputType},
            write_binding=saved_name_to_value.__setitem__,
        )
    assert order == normal_order == ["started", "execute", "validate", "inspect", "finished"]
    assert observed_events[0].assigned_binding_name_to_value == observed_events[1].assigned_binding_name_to_value == {"result": "result"}


def test_preparation_freezes_associations_but_keeps_objects_live(execute: Callable[..., StepResult]) -> None:
    shared: list[int] = []
    name_to_value: dict[str, object] = {"result": 0, "shared": shared}
    output_binding_names = ["result"]
    saved_name_to_value: dict[str, object] = {}

    def inspect(request: StepRequest) -> StepCandidate:
        name_to_value["result"] = "later"
        output_binding_names.clear()
        shared.append(1)
        assert request.step_context.environment.resolve("result") == 0
        assert request.step_context.environment.resolve("shared") is shared
        return StepCandidate(outcome=Pass(), binding_name_to_value={"result": "7"})

    with nh.run(InspectingExecutor(inspect)):
        execute("write", name_to_value=name_to_value, output_binding_names=output_binding_names, write_binding=saved_name_to_value.__setitem__)
    assert saved_name_to_value == {"result": 7}
    assert shared == [1]


@pytest.mark.parametrize("outcome", [nh.runtime.Break(), nh.runtime.Continue(), Return("12")])
def test_control_results_are_returned_after_saving(execute: Callable[..., StepResult], outcome: StepResult) -> None:
    saved_name_to_value: dict[str, object] = {}
    with nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=outcome, binding_name_to_value={"result": 7}))):
        result = execute(
            "control",
            name_to_value={},
            output_binding_names=("result",),
            is_in_loop=True,
            return_annotation=int,
            write_binding=saved_name_to_value.__setitem__,
        )
    assert result == (Return(12) if isinstance(outcome, Return) else outcome)
    assert saved_name_to_value == {"result": 7}


def test_deny_frontmatter_applies_before_saving(execute: Callable[..., StepResult]) -> None:
    saved_name_to_value: dict[str, object] = {}
    with nh.run(InspectingExecutor(lambda request: StepCandidate(outcome=Pass()))), pytest.raises(nh.ExecutionError, match="not allowed"):
        execute("---\ndeny: [pass]\n---\nReturn.", name_to_value={}, write_binding=saved_name_to_value.__setitem__)
    assert saved_name_to_value == {}


@pytest.mark.parametrize("names", [("result", "result"), ("not.valid",), ("return",), "result"])
def test_invalid_declarations_fail_preparation(execute: Callable[..., StepResult], names: Any) -> None:
    events: list[StepFinished] = []
    executor = ScriptedExecutor([])
    with nh.run(executor), nh.scope(lifecycle=StepLifecycle(events.append)), pytest.raises(nh.ExecutionError):
        execute("write", name_to_value={}, output_binding_names=names, write_binding=lambda name, value: None)
    assert executor.calls == []
    assert len(events) == 1 and isinstance(events[0], StepFailed)
    assert events[0].failure_stage == "preparation"
