"""Shared helpers for prompt-related tests."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic_ai.messages import BinaryContent, CachePoint, FileUrl, ImageUrl, TextContent, UploadedFile, UserContent

import nighthawk as nh
from nighthawk.runtime.prompt import build_user_prompt
from nighthawk.runtime.runner import _discover_implicit_type_alias_reference_names
from nighthawk.runtime.step_context import StepContext

_DEFAULT_EXECUTOR_CONFIGURATION = nh.StepExecutorConfiguration()


class FakeRunResult:
    def __init__(self, output):  # type: ignore[no-untyped-def]
        self.output = output


class FakeAgent:
    def __init__(self) -> None:
        self.seen_prompts: list[str | tuple[UserContent, ...]] = []

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


def build_user_prompt_text(
    *,
    processed_natural_program: str,
    step_context: StepContext,
    configuration: nh.StepExecutorConfiguration = _DEFAULT_EXECUTOR_CONFIGURATION,
) -> str:
    return prompt_content_to_text(
        build_user_prompt(
            processed_natural_program=processed_natural_program,
            step_context=step_context,
            configuration=configuration,
        )
    )


def _is_image_content_for_test(content: object) -> bool:
    if isinstance(content, ImageUrl):
        return True
    if isinstance(content, BinaryContent):
        return content.is_image
    media_type = getattr(content, "media_type", None)
    return isinstance(media_type, str) and media_type.startswith("image/")


def prompt_content_to_text(prompt_content: str | tuple[UserContent, ...]) -> str:
    if isinstance(prompt_content, str):
        return prompt_content

    text_part_list: list[str] = []

    for content in prompt_content:
        if isinstance(content, str):
            text_part_list.append(content)
            continue
        if isinstance(content, TextContent):
            text_part_list.append(content.content)
            continue
        if isinstance(content, (BinaryContent, FileUrl, UploadedFile)):
            text_part_list.append("<image>" if _is_image_content_for_test(content) else "<file>")
            continue
        if isinstance(content, CachePoint):
            continue
        raise TypeError(f"Unsupported UserContent type in test helper: {type(content).__name__}")

    return "".join(text_part_list)


def build_user_prompt_content(
    *,
    processed_natural_program: str,
    step_context: StepContext,
    configuration: nh.StepExecutorConfiguration = _DEFAULT_EXECUTOR_CONFIGURATION,
) -> tuple[UserContent, ...]:
    return build_user_prompt(
        processed_natural_program=processed_natural_program,
        step_context=step_context,
        configuration=configuration,
    )


def globals_section(prompt: str) -> str:
    return prompt.split("<<<NH:GLOBALS>>>\n", 1)[1].split("\n<<<NH:END_GLOBALS>>>", 1)[0]


def locals_section(prompt: str) -> str:
    return prompt.split("<<<NH:LOCALS>>>\n", 1)[1].split("\n<<<NH:END_LOCALS>>>", 1)[0]
