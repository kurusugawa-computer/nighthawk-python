"""Shared helpers for prompt-related tests."""

from __future__ import annotations

import nighthawk as nh
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
        from nighthawk.runtime.step_contract import PassStepOutcome

        self.seen_prompts.append(user_prompt)
        assert deps is not None
        _ = kwargs
        return FakeRunResult(PassStepOutcome(kind="pass"))


def build_step_context(*, python_globals: dict[str, object], python_locals: dict[str, object]) -> StepContext:
    return StepContext(
        step_id="test",
        step_globals=python_globals,
        step_locals=python_locals,
        binding_commit_targets=set(),
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
