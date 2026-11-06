"""Ordinary composable conversation judgments and actions.

Use a single judgment, an individual action, or the optional loop. Callers own
bindings, context selection, publication, and the surrounding run. The default
loop permits replacing either judgment or action with an ordinary function.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace

from ._conversation_judgment import choose_conversation_step
from ._conversation_records import (
    ContextItem,
    ContextPage,
    ContextQuery,
    ConversationAction,
    ConversationBindings,
    ConversationHistoryQuery,
    ConversationInput,
    ConversationPage,
    ConversationRecord,
    ConversationResult,
    ConversationStep,
    DataExport,
    FunctionDeclaration,
    FunctionSelection,
    SourceCandidate,
    SourceExport,
    SourceInvocation,
    SourceRetention,
    UserFacingBlock,
)
from ._host import current_grant
from ._proposals import CodeProposal
from .generated import retain_function
from .generation import FunctionRequest, propose_function
from .sources import export_data, export_source, invoke_source

__all__ = [
    "ContextItem",
    "ContextPage",
    "ContextQuery",
    "ConversationAction",
    "ConversationBindings",
    "ConversationHistoryQuery",
    "ConversationInput",
    "ConversationPage",
    "ConversationRecord",
    "ConversationResult",
    "ConversationStep",
    "DataExport",
    "FunctionDeclaration",
    "FunctionSelection",
    "SourceExport",
    "SourceInvocation",
    "SourceRetention",
    "UserFacingBlock",
    "choose_conversation_step",
    "converse",
    "perform_conversation_action",
]


def _reference(name: str) -> object:
    grant = current_grant()
    if name not in grant.references:
        raise LookupError(f"selected reference is unavailable: {name}")
    return grant.references[name]


def _candidate(name: str) -> SourceCandidate:
    candidate = _reference(name)
    if not isinstance(candidate, SourceCandidate):
        raise TypeError("source reuse requires an inert SourceCandidate")
    return candidate


async def perform_conversation_action(action: FunctionDeclaration | SourceInvocation | SourceExport | DataExport) -> object:
    """Execute one explicitly selected action, without making another judgment."""
    if isinstance(action, FunctionDeclaration):
        return retain_function(action.proposal, export_name=action.export_name)
    if isinstance(action, SourceInvocation):
        return await invoke_source(
            _candidate(action.candidate_name),
            export_name=action.export_name,
            argument_tuple=action.argument_tuple,
            keyword_argument_name_to_value=action.keyword_argument_name_to_value,
        )
    if isinstance(action, SourceExport):
        paths = export_source(_candidate(action.candidate_name), action.file_path)
    else:
        paths = export_data(_reference(action.reference_name), action.file_path)
    return {"exported_path_list": list(paths)}


async def converse(
    conversation_input: ConversationInput,
    *,
    bindings: ConversationBindings | None = None,
    choose_step: Callable[
        [ConversationInput, ConversationBindings, ConversationAction | None, object], Awaitable[ConversationStep]
    ] = choose_conversation_step,
    generate_function: Callable[[FunctionRequest, object], Awaitable[CodeProposal]] = propose_function,
    perform_action: Callable[[FunctionDeclaration | SourceInvocation | SourceExport | DataExport], Awaitable[object]] = perform_conversation_action,
) -> ConversationResult:
    """Compose next-step selection, optional source generation, and action execution.

    A final answer needs one judgment. A function request uses the independently
    replaceable source judgment before deterministic retention. Completed
    actions and values are explicit inputs to the next step; effects are never
    replayed to format a response. Returned writes remain unpublished values.
    """
    completed_action: ConversationAction | None = None
    action_result: object = None
    pending_name_to_value: dict[str, object] = {}
    if bindings is None:
        bindings = ConversationBindings()
    while True:
        step = await choose_step(conversation_input, bindings, completed_action, action_result)
        pending_name_to_value.update(step.binding_name_to_value)
        bindings = replace(bindings, binding_name_to_value={**bindings.binding_name_to_value, **step.binding_name_to_value})
        result = step.result
        if isinstance(result, ConversationResult):
            return replace(result, binding_name_to_value={**pending_name_to_value, **result.binding_name_to_value})
        try:
            action = result
            if isinstance(action, FunctionRequest):
                proposal = await generate_function(action, conversation_input)
                action = FunctionDeclaration(proposal, action.export_name)
            action_result = await perform_action(action)
            completed_action = result
        except Exception as error:
            if isinstance(result, (FunctionRequest, FunctionDeclaration)):
                raise
            return ConversationResult((UserFacingBlock("failure", f"Action unavailable or failed: {type(error).__name__}: {error}"),))
