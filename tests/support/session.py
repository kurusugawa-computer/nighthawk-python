from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from nighthawk.core.testing import ScriptedExecutor
from nighthawk.harness.perimeter import PerimeterPolicy
from nighthawk.harness.resources import bounded_resource_allocation
from nighthawk.harness.run_contract import AsyncScaffoldComposition, RunContract
from nighthawk.harness.runner import Runner
from nighthawk.parts.conversation import (
    ConversationInput,
    ConversationResult,
)
from nighthawk.session import (
    SessionManager,
)


def session_manager(*responses: Any) -> SessionManager:
    return SessionManager(
        runner=Runner(
            ScriptedExecutor(responses=list(responses)),
            PerimeterPolicy.NONE,
            lambda identity: bounded_resource_allocation(),
            accepts_aggregate_executor_accounting=True,
        )
    )


def configured(body: Callable[[ConversationInput], Awaitable[ConversationResult]], *, remove: tuple[str, ...] = ()) -> SessionManager:
    return SessionManager(
        runner=session_manager()._runner,
        conversation_composition_builder=lambda grant, policy: AsyncScaffoldComposition(
            RunContract(ConversationInput, ConversationResult),
            body,
            run_entry_grant=grant.without(remove),
            composition_perimeter_policy=policy,
        ),
    )
