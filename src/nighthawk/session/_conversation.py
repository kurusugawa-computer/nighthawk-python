"""Session binding and ownership adapter for the shared conversation parts."""

from collections.abc import Awaitable, Callable

from nighthawk.harness.grants import Grant
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.proposals import CodeProposal
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract
from nighthawk.parts.conversation import (
    ConversationAction,
    ConversationBindings,
    ConversationInput,
    ConversationResult,
    ConversationStep,
    DataExport,
    FunctionDeclaration,
    SourceExport,
    SourceInvocation,
    choose_conversation_step,
    converse,
)
from nighthawk.parts.conversation import perform_conversation_action as perform_shared_action
from nighthawk.parts.generation import FunctionRequest, propose_function
from nighthawk.session._bindings import _current_bindings, _SessionBindings
from nighthawk.session.errors import SessionLookupError


async def perform_conversation_action(action: FunctionDeclaration | SourceInvocation | SourceExport | DataExport) -> object:
    """Adapt callable retention; other operations use the shared parts directly."""
    if isinstance(action, FunctionDeclaration):
        bindings = _current_bindings.get()
        if bindings is None or bindings.declare_function is None:
            raise SessionLookupError("Function declaration requires an active Session conversation")
        return bindings.declare_function(action)
    return await perform_shared_action(action)


def build_conversation_composition(
    run_entry_grant: Grant = Grant.EMPTY,
    composition_perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE,
    *,
    choose_step: Callable[
        [ConversationInput, ConversationBindings, ConversationAction | None, object], Awaitable[ConversationStep]
    ] = choose_conversation_step,
    generate_function: Callable[[FunctionRequest, object], Awaitable[CodeProposal]] = propose_function,
) -> AsyncScaffoldComposition[ConversationInput, ConversationResult]:
    """Connect shared judgments and actions to Session-owned binding publication.

    Individual judgments can be replaced with ordinary functions while keeping
    the same Session lifecycle, grant ceiling, and Harness admission.
    """

    async def body(conversation_input: ConversationInput) -> ConversationResult:
        selected = _current_bindings.get() or _SessionBindings((), (), {})
        return await converse(
            conversation_input,
            bindings=ConversationBindings(selected.read_name_tuple, selected.write_name_tuple, selected.binding_name_to_type),
            choose_step=choose_step,
            generate_function=generate_function,
            perform_action=perform_conversation_action,
        )

    return AsyncScaffoldComposition(
        RunContract(ConversationInput, ConversationResult),
        body,
        run_entry_grant=run_entry_grant,
        composition_perimeter_policy=composition_perimeter_policy,
        function_definition_tuple=(choose_step, generate_function),
    )


type ConversationCompositionBuilder = Callable[[Grant, PerimeterPolicy], AsyncScaffoldComposition[ConversationInput, ConversationResult]]
