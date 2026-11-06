"""Session binding and ownership adapter for the shared conversation parts."""

from collections.abc import Awaitable, Callable

from .._grants import Grant
from .._perimeter import PerimeterPolicy
from .._proposals import CodeProposal
from ..conversation import ConversationAction, ConversationBindings, ConversationStep, choose_conversation_step, converse
from ..generation import FunctionRequest, propose_function
from ..runtime import AsyncScaffoldComposition, RunContract
from ._actions import perform_conversation_action
from ._bindings import _current_bindings, _SessionBindings
from ._records import ConversationInput, ConversationResult


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
