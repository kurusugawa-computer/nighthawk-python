"""Connect shared conversation actions to Session-owned callable entry."""

from ..conversation import perform_conversation_action as perform_shared_action
from ._bindings import _current_bindings
from ._records import DataExport, FunctionDeclaration, SourceExport, SourceInvocation
from .errors import SessionLookupError


async def perform_conversation_action(action: FunctionDeclaration | SourceInvocation | SourceExport | DataExport) -> object:
    """Adapt callable retention; other operations use the shared parts directly."""
    if isinstance(action, FunctionDeclaration):
        bindings = _current_bindings.get()
        if bindings is None or bindings.declare_function is None:
            raise SessionLookupError("Function declaration requires an active Session conversation")
        return bindings.declare_function(action)
    return await perform_shared_action(action)
