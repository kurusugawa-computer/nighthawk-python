"""Persistent generated definitions with Session-owned call entry and run-owned materialization."""

from __future__ import annotations

import weakref
from dataclasses import dataclass
from inspect import Signature
from typing import TYPE_CHECKING, TypeIs

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from .._dispatch import GeneratedCallableReference
from .._host import current_host
from ..generated import RetainedFunction, RetainedFunctionDefinition, retain_function
from ._records import FunctionDeclaration
from .errors import SessionClosedError, SessionLookupError

if TYPE_CHECKING:
    from ._session import Session


@dataclass(frozen=True, eq=False, repr=False)
class SessionFunction:
    """A callable definition that survives its declaration run, but never ratifies its source.

    Each Python-cell call enters a new run.
    Calls inside a run use its current grant and one cached materialization, resolving declared dependencies again on every invocation.
    Hidden state is not preserved between runs.
    Asynchronous definitions return a coroutine and require awaiting.
    """

    retained_function: RetainedFunction
    originating_cell_id: str
    _owner: weakref.ReferenceType[Session]

    @property
    def definition(self) -> RetainedFunctionDefinition:
        return self.retained_function.definition

    @property
    def __signature__(self) -> Signature:
        return self.retained_function.__signature__

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: object, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        # A write binding accepts an existing callable by identity.
        # Dictionaries describing Session ownership or live functions cannot construct one through validation.
        return core_schema.is_instance_schema(cls)

    @property
    def __name__(self) -> str:
        return self.definition.export_name

    @property
    def is_async(self) -> bool:
        return self.definition.is_async

    def __nighthawk_ledger_snapshot__(self) -> RetainedFunctionDefinition:
        return self.definition

    def _session(self) -> Session:
        session = self._owner()
        if session is None:
            raise SessionClosedError("The function's Session no longer exists")
        session._require_open()
        return session

    def _reference(self) -> GeneratedCallableReference:
        self._session()
        return self.retained_function.materialize()

    def __call__(self, *positional_arguments: object, **keyword_argument_name_to_value: object) -> object:
        session = self._session()
        if self.is_async:
            return session._invoke_generated_async(self, positional_arguments, keyword_argument_name_to_value)
        return session._invoke_generated(self, positional_arguments, keyword_argument_name_to_value)

    def __repr__(self) -> str:
        prefix = "async " if self.is_async else ""
        return f"SessionFunction({prefix}{self.__name__}, definition_id={self.definition.definition_id!r})"


def _is_session_function(value: object) -> TypeIs[SessionFunction]:
    """Inspect type identity without evaluating an arbitrary object's __class__."""

    return issubclass(type(value), SessionFunction)


def _declare_function(session: Session, request: FunctionDeclaration) -> SessionFunction:
    """Validate and materialize before offering a callable to ordinary write bindings."""

    session._require_open()
    host = current_host()
    execution = session._active_execution
    if host is None or execution is None:
        raise SessionLookupError("Function declaration requires an active Session run")
    function = retain_function(request.proposal, export_name=request.export_name)
    return SessionFunction(function, execution.cell_id, weakref.ref(session))
