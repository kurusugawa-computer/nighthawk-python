"""Shared helpers for prompt-related tests."""

from __future__ import annotations

from collections.abc import Iterable

import nighthawk as nh
from nighthawk.runtime.runner import _discover_implicit_type_alias_reference_names
from nighthawk.runtime.step_context import StepContext
from nighthawk.runtime.step_executor import build_user_prompt

_DEFAULT_EXECUTOR_CONFIGURATION = nh.StepExecutorConfiguration()


class FakeRunResult:
    def __init__(self, output):  # type: ignore[no-untyped-def]
        self.output = output


class FakeAgent:
    def __init__(self) -> None:
        self.seen_prompts: list[str] = []

    def run_sync(self, user_prompt, *, deps=None, **kwargs):  # type: ignore[no-untyped-def]
        from nighthawk.runtime.step_contract import PassStepOutcome, StepFinalResult

        self.seen_prompts.append(user_prompt)
        assert deps is not None
        _ = kwargs
        return FakeRunResult(StepFinalResult(result=PassStepOutcome(kind="pass")))


def build_step_context(
    *,
    python_globals: dict[str, object],
    python_locals: dict[str, object],
    input_binding_names: Iterable[str] = (),
) -> StepContext:
    implicit_type_alias_reference_names = _discover_implicit_type_alias_reference_names(
        step_locals=python_locals,
        step_globals=python_globals,
        input_binding_names=input_binding_names,
    )
    return StepContext(
        step_id="test",
        step_globals=python_globals,
        step_locals=python_locals,
        binding_commit_targets=set(),
        read_binding_names=frozenset(),
        implicit_reference_name_to_value={
            implicit_type_alias_reference_name: python_globals[implicit_type_alias_reference_name]
            for implicit_type_alias_reference_name in implicit_type_alias_reference_names
            if implicit_type_alias_reference_name in python_globals
        },
    )


def build_user_prompt_text(*, processed_natural_program: str, step_context: StepContext) -> str:
    return build_user_prompt(
        processed_natural_program=processed_natural_program,
        step_context=step_context,
        configuration=_DEFAULT_EXECUTOR_CONFIGURATION,
    )


def globals_section(prompt: str) -> str:
    return prompt.split("<<<NH:GLOBALS>>>\n", 1)[1].split("\n<<<NH:END_GLOBALS>>>", 1)[0]


def locals_section(prompt: str) -> str:
    return prompt.split("<<<NH:LOCALS>>>\n", 1)[1].split("\n<<<NH:END_LOCALS>>>", 1)[0]
