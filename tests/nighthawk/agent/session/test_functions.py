"""Session callables retain definitions, with generated execution admitted anew in each run."""

from __future__ import annotations

import inspect
from dataclasses import replace
from typing import Any

import pytest

from nighthawk.agent import (
    CodeProposal,
    DefinitionMaterializationEvent,
    DependencyDeclaration,
    DispatchEvent,
    DispatchRejectedError,
    NaturalExecutionOutcomeEvent,
    RunEndEvent,
    current_grant,
    scope,
)
from nighthawk.agent.runtime import AsyncScaffoldComposition, RunContract, unconfirmed_perimeter_confinement
from nighthawk.agent.session import (
    CellInput,
    ConversationInput,
    ConversationResult,
    FunctionDeclaration,
    SessionClosedError,
    SessionFunction,
    SessionManager,
)
from nighthawk.agent.session._actions import perform_conversation_action
from nighthawk.runtime.assignment import assign_expression_async
from nighthawk.runtime.expression import evaluate_expression_async
from nighthawk.runtime.step_outcome import Pass
from nighthawk.runtime.step_request import StepCandidate, StepRequest

from ..conftest import make_runner

FIBONACCI = CodeProposal(
    source="def fib(n: int) -> int:\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n",
    export_name_tuple=("fib",),
)


class DeclarationExecutor:
    """Exercise the same typed action and core assignment sequence used by the model."""

    def __init__(self, proposal: CodeProposal = FIBONACCI) -> None:
        self.proposal = proposal

    async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
        step_context = request.step_context
        if step_context.step_locals["action_result"] is None:
            return StepCandidate(
                outcome=Pass(), binding_name_to_value={"result": FunctionDeclaration(self.proposal, self.proposal.export_name_tuple[0])}
            )
        await assign_expression_async(step_context, "fib", "action_result")
        return StepCandidate(
            outcome=Pass(), binding_name_to_value={"result": ConversationResult(()), "fib": step_context.environment.step_name_to_value["fib"]}
        )


def declaration_session(proposal: CodeProposal = FIBONACCI) -> Any:
    # Confirmation doubles exercise library mediation, not deployment confinement.
    return SessionManager(runner=replace(make_runner(), step_executor=DeclarationExecutor(proposal))).create_session()


def declare(session: Any) -> SessionFunction:
    outcome = session.submit(CellInput("Declare a Fibonacci function <:fib>.")).wait()
    assert outcome.status == "success", outcome.output_tuple
    function = session.binding_name_to_value["fib"]
    assert isinstance(function, SessionFunction)
    assert function.definition.originating_run_id == outcome.run_id_tuple[0]
    assert function.originating_cell_id == outcome.cell_id
    assert any(output.kind == "publication" and output.text == "defined fib" for output in outcome.output_tuple)
    ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
    assert len(ledger.select(DispatchEvent)) == 1  # Declaration never invokes the body.
    assert ledger.select(RunEndEvent)
    return function


def test_declare_then_python_call_has_fresh_run_and_generated_provenance() -> None:
    session = declaration_session()
    try:
        function = declare(session)
        assert callable(function)
        assert str(inspect.signature(function)) == "(n: int) -> int"
        assert session._sources.approval_for(function) is None
        for source in ("answer = fib(3)", "answer = fib(n=3)"):
            outcome = session.submit(CellInput(source, mode="python")).wait()
            assert outcome.status == "success", outcome.output_tuple
            assert session.binding_name_to_value["answer"] == 2
            assert len(outcome.run_id_tuple) == 1
            assert outcome.run_id_tuple[0] != function.definition.originating_run_id
            ledger = session._run_id_to_ledger[outcome.run_id_tuple[0]]
            assert len(ledger.select(DispatchEvent)) == 2
            assert not ledger.select(NaturalExecutionOutcomeEvent)
            events = ledger.select(DefinitionMaterializationEvent)
            assert all(event.event.source_provenance_class == "generated" for event in events)
    finally:
        session.close()
    with pytest.raises(SessionClosedError):
        function(3)


@pytest.mark.parametrize("explicit", [False, True])
def test_natural_discovery_and_repeated_calls_share_materialization(explicit: bool) -> None:
    session = declaration_session()
    try:
        declare(session)

        class Executor:
            async def run_step_async(self, *, request: StepRequest) -> StepCandidate:
                step_context = request.step_context
                expression = "fib" if explicit else 'resolve_session_function(FunctionSelection(binding_name="fib"))'
                await assign_expression_async(step_context, "selected", expression)
                assert await evaluate_expression_async(step_context, "selected(3)") == 2
                assert await evaluate_expression_async(step_context, "selected(10)") == 55
                return StepCandidate(outcome=Pass(), binding_name_to_value={"result": ConversationResult(()), "answer": 55})

        session._runner = replace(session._runner, step_executor=Executor())
        outcome = session.submit(CellInput(f"Call {'<fib>' if explicit else 'fib'} and write <:answer>.")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 55
        assert len(outcome.run_id_tuple) == 1
        assert len(session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DispatchEvent)) == 3
    finally:
        session.close()


def test_alias_and_redefinition_preserve_identity_and_ordinary_callable_annotation() -> None:
    session = declaration_session()
    try:
        session.submit(CellInput("from collections.abc import Callable\nfib: Callable[[int], int]", mode="python")).wait()
        original = declare(session)
        session.submit(CellInput("previous = fib", mode="python")).wait()
        replacement = CodeProposal(source="def fib(n: int) -> int:\n    return n + 100\n", export_name_tuple=("fib",))
        session._runner = replace(session._runner, step_executor=DeclarationExecutor(replacement))
        new = declare(session)
        assert new is not original
        outcome = session.submit(CellInput("answers = (previous(3), fib(3))", mode="python")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answers"] == (2, 103)
        assert len(outcome.run_id_tuple) == 2
    finally:
        session.close()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_sync_and_async_calls_work_on_an_existing_event_loop(asynchronous: bool) -> None:
    prefix = "async " if asynchronous else ""
    proposal = CodeProposal(source=f"{prefix}def fib(n: int) -> int:\n    return n + 1\n", export_name_tuple=("fib",))
    session = declaration_session(proposal)
    try:
        outcome = await session.submit(CellInput("Declare <:fib>.")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        invocation = "await fib(3)" if asynchronous else "fib(3)"
        outcome = await session.submit(CellInput(f"answer = {invocation}", mode="python")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 4
        assert len(outcome.run_id_tuple) == 1
    finally:
        session.close()


def test_dependencies_are_resolved_at_every_invocation_and_attenuation_is_preserved() -> None:
    proposal = CodeProposal(
        source="def fib(n: int) -> int:\n    return n + offset\n",
        export_name_tuple=("fib",),
        dependency_tuple=(DependencyDeclaration(name="offset", kind="reference", declared_type=int),),
    )
    session = declaration_session(proposal)
    try:
        session.binding_name_to_value["offset"] = 1
        function = declare(session)
        session.binding_name_to_value["offset"] = 10
        outcome = session.submit(CellInput("answer = fib(3)", mode="python")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["answer"] == 13

        async def body(value: ConversationInput) -> ConversationResult:
            assert function(3) == 13
            with scope(current_grant().without(("offset",))), pytest.raises(DispatchRejectedError, match="dependency_missing"):
                function(3)
            assert function(4) == 14
            return ConversationResult(())

        session._builder = lambda grant, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult), body, run_entry_grant=grant, composition_perimeter_policy=policy
        )
        outcome = session.submit(CellInput("Call with narrowed authority.")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert len(outcome.run_id_tuple) == 1
        assert len(session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DispatchEvent)) == 3
        for value in (None, "wrong"):
            if value is None:
                del session.binding_name_to_value["offset"]
            else:
                session.binding_name_to_value["offset"] = value
            outcome = session.submit(CellInput("answer = fib(3)", mode="python")).wait()
            assert outcome.status == "failure"
            assert session.binding_name_to_value["answer"] == 13
            assert not session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DispatchEvent)
    finally:
        session.close()


@pytest.mark.parametrize("failure", ["source", "confinement", "write_type", "undeclared"])
def test_failed_declaration_or_write_set_keeps_all_original_bindings(failure: str) -> None:
    session = declaration_session()
    try:
        original = declare(session)
        session.binding_name_to_value["other"] = 1
        if failure == "source":
            proposal = CodeProposal(source="def fib(:\n", export_name_tuple=("fib",))
            session._runner = replace(session._runner, step_executor=DeclarationExecutor(proposal))
        elif failure == "confinement":
            session._runner = replace(session._runner, perimeter_confinement=unconfirmed_perimeter_confinement())
        else:

            async def body(value: ConversationInput) -> ConversationResult:
                function = await perform_conversation_action(FunctionDeclaration(FIBONACCI, "fib"))
                return ConversationResult((), {"fib": function, "other": "invalid" if failure == "write_type" else 2})

            session._builder = lambda grant, policy: AsyncScaffoldComposition(
                RunContract(ConversationInput, ConversationResult), body, run_entry_grant=grant, composition_perimeter_policy=policy
            )
        task = "Declare <:fib> and update <:other>." if failure == "write_type" else "Declare <:fib>."
        outcome = session.submit(CellInput(task)).wait()
        assert outcome.status == "failure", outcome.output_tuple
        assert session.binding_name_to_value["fib"] is original
        assert session.binding_name_to_value["other"] == 1
        assert not any(output.kind == "publication" for output in outcome.output_tuple)
    finally:
        session.close()


def test_mutable_default_state_is_run_local() -> None:
    proposal = CodeProposal(
        source="def fib(n: int, values: list[int] = []) -> int:\n    values.append(n)\n    return len(values)\n", export_name_tuple=("fib",)
    )
    session = declaration_session(proposal)
    try:
        function = declare(session)
        for _ in range(2):
            outcome = session.submit(CellInput("answer = fib(3)", mode="python")).wait()
            assert outcome.status == "success", outcome.output_tuple
            assert session.binding_name_to_value["answer"] == 1

        async def body(value: ConversationInput) -> ConversationResult:
            assert function(3) == 1
            assert function(3) == 2
            return ConversationResult(())

        session._builder = lambda grant, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult), body, run_entry_grant=grant, composition_perimeter_policy=policy
        )
        outcome = session.submit(CellInput("Call twice in one run.")).wait()
        assert outcome.status == "success", outcome.output_tuple
    finally:
        session.close()


def test_ordinary_natural_block_can_write_the_same_callable_without_conversion() -> None:
    session = declaration_session()
    try:
        function = declare(session)

        class Executor:
            async def run_step_async(self, **options: Any) -> Any:
                return StepCandidate(outcome=Pass(), binding_name_to_value={"result": function})

        session._runner = replace(session._runner, step_executor=Executor())
        source = '''from collections.abc import Callable
@nh.function
def select() -> Callable[[int], int]:
    result: Callable[[int], int] = fib
    """execute
    Assign the supplied function to <:result>.
    """
    return result
selected = select()
answer = selected(3)
'''
        outcome = session.submit(CellInput(source, mode="python")).wait()
        assert outcome.status == "success", outcome.output_tuple
        assert session.binding_name_to_value["selected"] is function
        assert session.binding_name_to_value["answer"] == 2
        assert len(outcome.run_id_tuple) == 2
    finally:
        session.close()


@pytest.mark.parametrize(
    "proposal,export_name",
    [
        (FIBONACCI, "missing"),
        (CodeProposal(source="number: int = 2", export_name_tuple=("number",)), "number"),
        (
            CodeProposal(
                source=FIBONACCI.source,
                export_name_tuple=("fib",),
                execution_intent="definition_and_invocation",
                entry_export_name="fib",
                entry_argument_tuple=(3,),
            ),
            "fib",
        ),
    ],
)
def test_declaration_requires_a_definition_only_callable_export(proposal: CodeProposal, export_name: str) -> None:
    async def body(value: ConversationInput) -> ConversationResult:
        function = await perform_conversation_action(FunctionDeclaration(proposal, export_name))
        return ConversationResult((), {"fib": function})

    session = SessionManager(
        runner=make_runner(),
        conversation_composition_builder=lambda grant, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult), body, run_entry_grant=grant, composition_perimeter_policy=policy
        ),
    ).create_session()
    try:
        outcome = session.submit(CellInput("Declare <:fib>.")).wait()
        assert outcome.status == "failure", outcome.output_tuple
        assert "fib" not in session.binding_name_to_value
        assert all(
            entry.event.dispatch_kind == "definition_only"
            for entry in session._run_id_to_ledger[outcome.run_id_tuple[0]].select(DispatchEvent)
            if isinstance(entry.event, DispatchEvent)
        )
    finally:
        session.close()


def test_private_callable_discovery_requires_explicit_read_and_closed_coroutine_cannot_start() -> None:
    proposal = CodeProposal(source="async def fib(n: int) -> int:\n    return n + 1\n", export_name_tuple=("fib",))
    session = declaration_session(proposal)
    try:
        function = declare(session)
        session.submit(CellInput("_private = fib\ndel fib", mode="python")).wait()
        assert "_private" not in session._selection()[1]
        assert "_private" in session._selection(("_private",))[1]
        pending = function(3)
        assert inspect.iscoroutine(pending)
        session.close()
        with pytest.raises(SessionClosedError):
            pending.send(None)
    finally:
        session.close()
