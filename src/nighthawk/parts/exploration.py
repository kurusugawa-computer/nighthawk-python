"""An optional fixed-loop Agent Part over the shared Harness.

The loop is one long-lived Natural-function invocation whose ordinary Python
locals retain task-specific state and generated callable references. Each
iteration asks one Natural block for exactly one typed transition: a code
proposal to extend the run's capabilities, a bounded note to retain, or the
final summary. Deterministic Python submits proposals through host-owned
dispatch and decides when to return. No directive vocabulary, phase sequence,
transcript, or completion gate is part of the substrate; this transition
structure is an example-level composition choice.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

import nighthawk as nh
from nighthawk.harness.dispatch import DispatchResult, GeneratedCallableReference, dispatch
from nighthawk.harness.errors import DispatchRejectedError, GeneratedExecutionError
from nighthawk.harness.grants import Grant
from nighthawk.harness.perimeter import (
    EffectFootprint,  # noqa: F401 - constructor visible to Natural source
    PerimeterPolicy,
)
from nighthawk.harness.proposals import (
    CodeProposal,
    DependencyDeclaration,  # noqa: F401 - constructor visible to Natural source
)
from nighthawk.harness.run_contract import RunContract, ScaffoldComposition

__all__ = ["ExplorationTask", "ExplorationResult", "explore", "exploration_composition"]


MAXIMUM_NOTE_CHARACTER_COUNT = 2_000
"""Bound on the note retained across iterations; longer notes are truncated and flagged."""


class ExplorationTask(BaseModel):
    """Run input of the optional reference loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task: str = Field(min_length=1)


class ExplorationResult(BaseModel):
    """Run result of the optional reference loop."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(min_length=1)
    generated_function_name_tuple: tuple[str, ...] = ()


class RetainedNote(BaseModel):
    """Bounded typed note carried from one iteration to the next."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    truncated: bool = False

    @classmethod
    def from_text(cls, text: str) -> RetainedNote:
        if len(text) <= MAXIMUM_NOTE_CHARACTER_COUNT:
            return cls(text=text)
        return cls(text=text[:MAXIMUM_NOTE_CHARACTER_COUNT], truncated=True)


def _describe_dispatch(result: DispatchResult) -> str:
    names = ", ".join(definition.export_name for definition in result.definition_tuple)
    if result.entry_result is None:
        return f"Defined generated functions: {names}."
    return f"Defined generated functions: {names}. Entry result type: {type(result.entry_result).__name__}."


@nh.function
def explore(run_input: ExplorationTask) -> ExplorationResult:
    """Advance ``run_input.task`` until the Natural block declares it complete.

    One Natural block per iteration selects exactly one typed transition. The
    block sees the task, the current retained note, and the generated functions
    materialized so far, plus the capabilities of the grant in effect. Generated
    functions are ordinary callables the block may invoke directly.
    A missing or ambiguous transition retains correction guidance for the next judgment instead of dispatching source or accepting completion.
    Operations already performed by the judgment remain observed effects, without replay.
    """

    task = run_input.task  # noqa: F841 - read by the Natural block
    note: RetainedNote | None = None
    generated_function_tuple: tuple[GeneratedCallableReference, ...] = ()
    while True:
        code_proposal: CodeProposal | None = None
        next_note: str | None = None
        final_summary: str | None = None
        """execute
        ---
        deny: [raise, return, break, continue]
        ---
        Advance <task> from what is currently true: the retained <note> (null when nothing was retained)
        and the generated functions in <generated_function_tuple>, which you may call directly.
        Choose exactly one transition and leave the other write bindings null:
        - set <:code_proposal> to CodeProposal(source=..., export_name_tuple=(...),
          dependency_tuple=(DependencyDeclaration(name=..., kind="capability" or "reference", declared_type=...), ...),
          effect_footprint=EffectFootprint(capability_name_set={...})) whose source defines new Python
          functions that will help with <task>; declare every export and every nonlocal name it uses,
          giving each capability dependency its signature text exactly as shown (for example "(name: str) -> str")
          and each reference dependency the Python type it must satisfy (for example str);
        - set <:next_note> to a short note worth retaining for the next iteration after doing work
          with the available functions and capabilities;
        - set <:final_summary> to a concise summary once <task> is complete.
        """
        transition_count = sum(value is not None for value in (code_proposal, next_note, final_summary))
        if transition_count != 1:
            note = RetainedNote.from_text(
                f"Expected exactly one transition; received {transition_count}. "
                "Set exactly one of code_proposal, next_note, or final_summary and leave the others null."
            )
            continue
        if final_summary is not None:
            return ExplorationResult(
                summary=final_summary,
                generated_function_name_tuple=tuple(reference.definition.export_name for reference in generated_function_tuple),
            )
        if code_proposal is not None:
            try:
                dispatch_result = dispatch(code_proposal)
            except (DispatchRejectedError, GeneratedExecutionError) as failure:
                note = RetainedNote.from_text(f"Dispatch failed: {type(failure).__name__}: {failure}")
                continue
            generated_function_tuple = (*generated_function_tuple, *dispatch_result.reference_tuple)
            note = RetainedNote.from_text(_describe_dispatch(dispatch_result))
            continue
        if next_note is not None:
            note = RetainedNote.from_text(next_note)  # noqa: F841 - read by the Natural block


def exploration_composition(
    *,
    run_entry_grant: Grant = Grant.EMPTY,
    composition_perimeter_policy: PerimeterPolicy = PerimeterPolicy.NONE,
) -> ScaffoldComposition[ExplorationTask, ExplorationResult]:
    """Return the optional reference loop as a scaffold composition."""

    return ScaffoldComposition(
        run_contract=RunContract(input_type=ExplorationTask, result_type=ExplorationResult),
        body=explore,
        run_entry_grant=run_entry_grant,
        composition_perimeter_policy=composition_perimeter_policy,
        function_definition_tuple=(explore,),
    )
