from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict

from nighthawk.harness.grants import Grant
from nighthawk.harness.perimeter import PerimeterPolicy


class RunIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str


@dataclass(frozen=True)
class RunContract[T, R]:
    """The typed Python call boundary of one agent run.

    Ordinary application classes use identity-preserving instance checks.
    Annotated constraints and types with their own validation retain that validation;
    instance checks do not validate an arbitrary object's internal state.
    """

    input_type: type[T]
    result_type: type[R]


@dataclass(frozen=True)
class ApprovedFunctionDefinition:
    """Host-supplied association with an existing independent approval decision.

    This records approval evidence supplied at run entry; constructing it does
    not approve source. The application owns the decision and the exact
    source/callable correspondence. An omitted wrapper uses ordinary repository
    provenance for the callable. Neither route grants invocation authority.
    """

    function: Callable[..., object]
    source_identity: str
    approval_identity: str
    approval_route: str
    originating_cell_id: str | None = None


@dataclass(frozen=True)
class ScaffoldComposition[T, R]:
    """A deterministic Python composition that configures and invokes the substrate.

    ``body`` is the run's control flow. ``run_entry_grant`` and
    ``composition_perimeter_policy`` form the authority ceiling admitted at run
    entry. ``function_definition_tuple`` lists the initial ratified function
    definitions recorded at the run root.
    """

    run_contract: RunContract[T, R]
    body: Callable[[T], R]
    run_entry_grant: Grant = field(default_factory=lambda: Grant.EMPTY)
    composition_perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE
    function_definition_tuple: tuple[Callable[..., object] | ApprovedFunctionDefinition, ...] = ()


@dataclass(frozen=True)
class AsyncScaffoldComposition[T, R]:
    """An awaited composition whose contract describes its completed result.

    The run contract describes the completed result, while body describes the
    awaitable that produces it. The separate shape makes run dispatch typed.
    """

    run_contract: RunContract[T, R]
    body: Callable[[T], Awaitable[R]]
    run_entry_grant: Grant = field(default_factory=lambda: Grant.EMPTY)
    composition_perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE
    function_definition_tuple: tuple[Callable[..., object] | ApprovedFunctionDefinition, ...] = ()
