"""Retained generated functions, independent of a Session or storage backend.

Retention preserves source and observed definition identity without extending a run or its invocation authority.
Execution requires an active run and uses its current grant.
Applications own retention; approval is a separate, external decision.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import cast

from pydantic import GetCoreSchemaHandler
from pydantic_core import core_schema

from nighthawk.harness.dispatch import AsyncGeneratedCallableReference, GeneratedCallableReference, _materialize_retained_function, _retain_function
from nighthawk.harness.proposals import CodeProposal

__all__ = ["RetainedFunction", "RetainedFunctionDefinition", "retain_function"]


@dataclass(frozen=True)
class RetainedFunctionDefinition:
    """Exact generated source and its originally observed callable definition.

    ``definition_id`` identifies the original materialization, not a grant or approval.
    Later materializations record that identity while receiving new run-local definition identities.
    Dependencies resolve from each caller.
    """

    definition_id: str
    proposal: CodeProposal
    export_name: str
    is_async: bool
    originating_run_id: str


@dataclass(frozen=True, eq=False, repr=False, init=False)
class RetainedFunction:
    """An ordinary callable over retained source and fresh host admission.

    The Harness materializes once per object identity per run.
    Every invocation still checks the current dependencies, grant, allocation, and confinement.
    Mutable defaults and private closure state are local to a materialization; explicitly pass any state that should survive a run.
    Asynchronous functions return an unstarted coroutine; awaiting it performs materialization and invocation admission.
    """

    definition: RetainedFunctionDefinition
    __signature__: inspect.Signature

    def __init__(self) -> None:
        raise TypeError("Use retain_function to retain an observed generated definition")

    @classmethod
    def _from_materialization(cls, definition: RetainedFunctionDefinition, signature: inspect.Signature) -> RetainedFunction:
        function = object.__new__(cls)
        object.__setattr__(function, "definition", definition)
        object.__setattr__(function, "__signature__", signature)
        return function

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: object, handler: GetCoreSchemaHandler) -> core_schema.CoreSchema:
        return core_schema.is_instance_schema(cls)

    @property
    def __name__(self) -> str:
        return self.definition.export_name

    @property
    def is_async(self) -> bool:
        return self.definition.is_async

    def __nighthawk_ledger_snapshot__(self) -> RetainedFunctionDefinition:
        return self.definition

    def materialize(self) -> GeneratedCallableReference:
        """Resolve a run-local reference under the active caller's authority.

        The returned reference expires with its run, even if this retained function is subsequently materialized in another run.
        """
        return _materialize_retained_function(self)

    def __call__(self, *positional_arguments: object, **keyword_argument_name_to_value: object) -> object:
        if self.is_async:
            return self._invoke_async(positional_arguments, keyword_argument_name_to_value)
        return self.materialize()(*positional_arguments, **keyword_argument_name_to_value)

    async def _invoke_async(self, arguments: tuple[object, ...], keyword_argument_name_to_value: dict[str, object]) -> object:
        reference = cast(AsyncGeneratedCallableReference, self.materialize())
        return await reference(*arguments, **keyword_argument_name_to_value)

    def __repr__(self) -> str:
        prefix = "async " if self.is_async else ""
        return f"RetainedFunction({prefix}{self.__name__}, definition_id={self.definition.definition_id!r})"


def retain_function(proposal: CodeProposal, *, export_name: str) -> RetainedFunction:
    """Validate and materialize a definition-only proposal without invoking it.

    Return the selected callable's retained definition for explicit later use.
    Other declared exports are materialized by the same dispatch.
    No catalog, persistence, Session, or approval workflow is required or installed.
    """
    return _retain_function(proposal, export_name=export_name)
