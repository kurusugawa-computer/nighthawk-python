from __future__ import annotations

from dataclasses import replace
from functools import partial

from nighthawk.core.step_request import StepCandidate, StepRequest
from nighthawk.core.testing import CallbackExecutor, ScriptedExecutor
from nighthawk.harness.context import current_capabilities
from nighthawk.harness.generated import RetainedFunction
from nighthawk.harness.grants import Capability, Grant
from nighthawk.harness.ledger import DispatchEvent
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract, ScaffoldComposition
from nighthawk.harness.run_handle import RunHandle
from nighthawk.parts.conversation import (
    ConversationAction,
    ConversationBindings,
    ConversationInput,
    ConversationResult,
    ConversationStep,
    FunctionDeclaration,
    UserFacingBlock,
    choose_conversation_step,
    converse,
    perform_conversation_action,
)
from nighthawk.parts.generation import FunctionRequest, propose_function
from nighthawk.session import CellInput, SessionFunction, SessionManager, build_conversation_composition
from tests.support.harness import make_runner

PROPOSAL = CodeProposal(source="def increment(value: int) -> int:\n    return value + 1\n", export_name_tuple=("increment",))
REQUEST = FunctionRequest("Return the integer argument plus one without calling the function yet.", "increment")


async def test_single_judgment_returns_action_and_writes_without_executing_or_publishing() -> None:
    selected_input = ConversationInput("Select a function", context={"domain": "integers"})
    bindings = ConversationBindings(write_name_tuple=("note",), binding_name_to_type={"note": str})

    def handler(request: StepRequest) -> StepCandidate:
        assert request.step_context.step_locals["conversation_input"] is selected_input
        assert request.step_context.step_locals["completed_action"] is None
        return StepCandidate(binding_name_to_value={"result": REQUEST, "note": "selected"})

    runner = replace(make_runner(), step_executor=CallbackExecutor(handler))
    handle = RunHandle()

    async def body(value: ConversationInput) -> ConversationStep:
        return await choose_conversation_step(value, bindings)

    result = await runner.run(AsyncScaffoldComposition(RunContract(ConversationInput, ConversationStep), body), selected_input, handle=handle)
    assert result.result == REQUEST
    assert result.binding_name_to_value == {"note": "selected"}
    assert bindings.binding_name_to_value == {}
    assert not handle.ledger.select(DispatchEvent)


async def test_generation_receives_explicit_context_and_dependency_descriptions_without_action_grants() -> None:
    def action() -> str:
        raise AssertionError("Generation must not invoke this action")

    capability = Capability(name="domain_action", function=action, authority_boundary="test action")
    context = {"expected": 7}

    def handler(request: StepRequest) -> StepCandidate:
        assert not current_capabilities()
        binding_name_to_value = request.step_context.step_locals
        assert binding_name_to_value["context"] is context
        assert binding_name_to_value["request"] == REQUEST
        declarations = binding_name_to_value["available_dependency_tuple"]
        assert isinstance(declarations, tuple)
        assert any(declaration.name == capability.name and declaration.declared_type == capability.declared_type for declaration in declarations)
        return StepCandidate(binding_name_to_value={"proposal": PROPOSAL})

    runner = replace(make_runner(), step_executor=CallbackExecutor(handler))

    async def body(value: None) -> CodeProposal:
        proposal = await propose_function(REQUEST, context)
        assert "domain_action" in current_capabilities()
        return proposal

    handle = RunHandle()
    result = await runner.run(
        AsyncScaffoldComposition(RunContract(type(None), CodeProposal), body, run_entry_grant=Grant.of(capability)), None, handle=handle
    )
    assert result == PROPOSAL
    assert not handle.ledger.select(DispatchEvent)


async def test_one_replaced_generation_part_is_used_by_batch_conversation_and_session() -> None:
    generated_contexts: list[object] = []

    async def generate(request: FunctionRequest, context: object) -> CodeProposal:
        assert request == REQUEST
        generated_contexts.append(context)
        return PROPOSAL

    async def choose(
        value: ConversationInput, bindings: ConversationBindings, completed: ConversationAction | None, result: object
    ) -> ConversationStep:
        if completed is None:
            return ConversationStep(REQUEST)
        assert completed == REQUEST
        assert isinstance(result, (RetainedFunction, SessionFunction))
        return ConversationStep(ConversationResult((UserFacingBlock("answer", "Declared increment."),), {"increment": result}))

    runner = make_runner()

    async def body(value: ConversationInput) -> ConversationResult:
        return await converse(value, choose_step=choose, generate_function=generate)

    input_value = ConversationInput("Declare increment", write_binding_name_tuple=("increment",))
    batch = await runner.run(AsyncScaffoldComposition(RunContract(ConversationInput, ConversationResult), body), input_value)
    retained = batch.binding_name_to_value["increment"]
    assert isinstance(retained, RetainedFunction)
    assert runner.run(ScaffoldComposition(RunContract(int, object), retained), 6) == 7

    session = SessionManager(
        runner=runner, conversation_composition_builder=partial(build_conversation_composition, choose_step=choose, generate_function=generate)
    ).create_session()
    try:
        outcome = await session.submit(CellInput("Declare <:increment>.")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        wrapped = session.binding_name_to_value["increment"]
        assert isinstance(wrapped, SessionFunction)
        assert session._sources.approval_for(wrapped) is None
        assert (await session.submit(CellInput("answer = increment(6)", mode="python")).wait_async()).status == "success"
        assert session.binding_name_to_value["answer"] == 7
        shared = wrapped.retained_function
    finally:
        assert session.close()
    assert runner.run(ScaffoldComposition(RunContract(int, object), shared), 9) == 10
    assert len(generated_contexts) == 2


async def test_default_selection_can_be_combined_with_a_replaced_generator_without_session() -> None:
    async def generate(request: FunctionRequest, context: object) -> CodeProposal:
        assert request == REQUEST
        return PROPOSAL

    def handler(request: StepRequest) -> StepCandidate:
        result = request.step_context.step_locals["action_result"]
        if result is None:
            return StepCandidate(binding_name_to_value={"result": REQUEST})
        assert isinstance(result, RetainedFunction)
        return StepCandidate(binding_name_to_value={"result": ConversationResult((UserFacingBlock("answer", "Declared."),), {"increment": result})})

    runner = replace(make_runner(), step_executor=CallbackExecutor(handler))

    async def body(value: ConversationInput) -> ConversationResult:
        return await converse(value, generate_function=generate)

    result = await runner.run(
        AsyncScaffoldComposition(RunContract(ConversationInput, ConversationResult), body), ConversationInput("Declare increment")
    )
    assert isinstance(result.binding_name_to_value["increment"], RetainedFunction)


async def test_direct_action_requires_no_conversation_loop_or_session() -> None:
    runner = replace(make_runner(), step_executor=ScriptedExecutor(responses=[]))

    async def body(value: None) -> object:
        return await perform_conversation_action(FunctionDeclaration(PROPOSAL, "increment"))

    function = await runner.run(AsyncScaffoldComposition(RunContract(type(None), object), body), None)
    assert isinstance(function, RetainedFunction)
    assert runner.run(ScaffoldComposition(RunContract(int, object), function), 6) == 7


async def test_session_admits_the_shared_natural_generator_declared_by_its_composition() -> None:
    def handler(request: StepRequest) -> StepCandidate:
        if "proposal" in request.output_binding_names:
            assert not current_capabilities()
            return StepCandidate(binding_name_to_value={"proposal": PROPOSAL})
        result = request.step_context.step_locals["action_result"]
        if result is None:
            return StepCandidate(binding_name_to_value={"result": REQUEST})
        return StepCandidate(binding_name_to_value={"result": ConversationResult(()), "increment": result})

    session = SessionManager(runner=replace(make_runner(), step_executor=CallbackExecutor(handler))).create_session()
    try:
        outcome = await session.submit(CellInput("Declare <:increment>.")).wait_async()
        assert outcome.status == "success", outcome.output_tuple
        function = session.binding_name_to_value["increment"]
        assert isinstance(function, SessionFunction)
        assert session._sources.approval_for(function) is None
        assert (await session.submit(CellInput("result = increment(6)", mode="python")).wait_async()).status == "success"
        assert session.binding_name_to_value["result"] == 7
    finally:
        assert session.close()
