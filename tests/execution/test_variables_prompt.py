import builtins
import functools
import textwrap
from collections import namedtuple
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel
from pydantic_ai.messages import AudioUrl, BinaryContent, ImageUrl, TextContent, UploadedFile

import nighthawk as nh
from nighthawk.resilience import fallback
from tests.execution.prompt_test_helpers import (
    FakeAgent,
    build_step_context,
    build_user_prompt_content,
    build_user_prompt_text,
    globals_section,
    locals_section,
    prompt_content_to_text,
)

_VALID_PNG_HEADER = b"\x89PNG\r\n\x1a\n"


class _CallableValue:
    def __call__(self, item):  # type: ignore[no-untyped-def]
        return item


class _BoundMethodCarrier:
    def transform(self, item):  # type: ignore[no-untyped-def]
        return item


class _SignatureUnavailableCallable:
    @property
    def __signature__(self):  # type: ignore[no-untyped-def]
        raise ValueError("signature unavailable")

    def __call__(self, *items):  # type: ignore[no-untyped-def]
        return items


class _PublicObjectSurface:
    field_value = 12
    _internal = "skip"

    def append(self, action: str) -> None:
        _ = action

    def remove(self, action_id: str) -> None:
        _ = action_id

    @property
    def computed(self) -> str:
        raise AssertionError("property must not be evaluated")


class _WithSlots:
    __slots__ = ("slot_name", "_private_slot")

    def __init__(self) -> None:
        self.slot_name = "slot"
        self._private_slot = "secret"


class _MethodCollisionA:
    def open(self, action_id: str) -> None:
        _ = action_id


class _MethodCollisionB:
    def open(self, action_id: str) -> None:
        _ = action_id


class _AsyncSurface:
    async def wait(self, operation: str) -> str:
        return operation


G = 1


def _global_helper(required, optional=10, *, keyword="x"):  # type: ignore[no-untyped-def]
    _ = keyword
    return required + optional


def _global_typed_helper(
    number: int,
    names: list[str],
    *,
    enabled: bool = True,
) -> dict[str, int]:
    _ = names
    _ = enabled
    return {"number": number}


def test_user_prompt_renders_globals_and_locals_for_references(tmp_path):
    _ = tmp_path
    agent = FakeAgent()
    with nh.run(nh.AgentStepExecutor.from_agent(agent=agent)):
        a = 1.0

        @nh.natural_function
        def f() -> None:
            x = 10
            """natural
            Say hi.
            """

            y = "hello"
            """natural
            <a><G>
            """

            _ = x
            _ = y

        f()

        _ = a

    assert prompt_content_to_text(agent.seen_prompts[0]) == textwrap.dedent(
        """\
        <<<NH:PROGRAM>>>
        Say hi.

        <<<NH:END_PROGRAM>>>

        <<<NH:LOCALS>>>
        a: float = 1.0
        x: int = 10
        <<<NH:END_LOCALS>>>

        <<<NH:GLOBALS>>>

        <<<NH:END_GLOBALS>>>
        """
    )
    assert prompt_content_to_text(agent.seen_prompts[1]) == textwrap.dedent(
        """\
        <<<NH:PROGRAM>>>
        <a><G>

        <<<NH:END_PROGRAM>>>

        <<<NH:LOCALS>>>
        a: float = 1.0
        x: int = 10
        y: str = "hello"
        <<<NH:END_LOCALS>>>

        <<<NH:GLOBALS>>>
        G: int = 1
        <<<NH:END_GLOBALS>>>
        """
    )


def test_locals_section_renders_plain_python_function_signature() -> None:
    def local_function(required, optional=10, *, keyword="x"):  # type: ignore[no-untyped-def]
        _ = keyword
        return required + optional

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"local_function": local_function},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "local_function: (required, optional=10, *, keyword='x')" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_locals_section_renders_lambda_builtin_and_bound_method_values() -> None:
    lambda_value = lambda number: number  # noqa: E731
    bound_method = _BoundMethodCarrier().transform

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "lambda_value": lambda_value,
            "builtin_length": len,
            "bound_method": bound_method,
        },
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "lambda_value: (number)" in locals_text
    assert "builtin_length: (obj, /)  # " in locals_text
    assert "bound_method: (item)" in locals_text
    assert "<function>" not in locals_text
    assert "builtin_function_or_method" not in locals_text
    assert "method = " not in locals_text


def test_locals_section_renders_callable_instance_and_partial_function() -> None:
    callable_instance = _CallableValue()
    partially_applied_helper = functools.partial(_global_helper, 1)

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "callable_instance": callable_instance,
            "partially_applied_helper": partially_applied_helper,
        },
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "callable_instance: (item)" in locals_text
    assert "partially_applied_helper: (optional=10, *, keyword='x')" in locals_text
    assert "partial = " not in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text
    assert "(*args, **kwargs)" not in locals_text


def test_globals_section_renders_referenced_global_function_signature() -> None:
    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "global_helper": _global_helper,
        },
        python_locals={},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Use <global_helper> in this block.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "global_helper: (required, optional=10, *, keyword='x')" in globals_text
    assert "# " not in globals_text
    assert "<function>" not in globals_text


def test_locals_section_renders_typed_function_argument_and_return_annotations() -> None:
    def local_typed_function(
        number: int,
        names: list[str],
        *,
        enabled: bool = True,
    ) -> dict[str, int]:
        _ = names
        _ = enabled
        return {"number": number}

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"local_typed_function": local_typed_function},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "local_typed_function: (number: int, names: list[str], *, enabled: bool = True) -> dict[str, int]" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_globals_section_renders_referenced_typed_function_argument_and_return_annotations() -> None:
    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "global_typed_helper": _global_typed_helper,
        },
        python_locals={},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Use <global_typed_helper> in this block.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "global_typed_helper: (number: int, names: list[str], *, enabled: bool = True) -> dict[str, int]" in globals_text
    assert "# " not in globals_text
    assert "<function>" not in globals_text


def test_signature_unavailable_callable_renders_non_invocable_marker() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"opaque_callable": _SignatureUnavailableCallable()},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "opaque_callable: <callable; signature-unavailable>" in locals_text
    assert "# " not in locals_text
    assert "(*args, **kwargs)" not in locals_text


def test_object_surface_renders_public_methods_and_fields_without_properties() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"collector": _PublicObjectSurface()},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect collector.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "collector: object = _PublicObjectSurface" in locals_text
    assert "collector.append: (action: str) -> None" in locals_text
    assert "collector.remove: (action_id: str) -> None" in locals_text
    assert "collector.field_value: int = 12" in locals_text
    assert "collector._internal" not in locals_text
    assert "collector.computed" not in locals_text


def test_object_surface_renders_public_slots() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"collector": _WithSlots()},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect collector.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert 'collector.slot_name: str = "slot"' in locals_text
    assert "collector._private_slot" not in locals_text


def test_object_method_signature_collision_renders_disambiguation_hint() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"a": _MethodCollisionA(), "b": _MethodCollisionB()},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect objects.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "a.open: (action_id: str) -> None  # disambiguation: use a.open" in locals_text
    assert "b.open: (action_id: str) -> None  # disambiguation: use b.open" in locals_text


def test_object_async_method_renders_async_marker() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"worker": _AsyncSurface()},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect worker.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "worker.wait: (operation: str) -> str  # async" in locals_text


def test_callable_signature_collision_renders_disambiguation_hint_for_each_callable() -> None:
    def open_customer(customer_id):  # type: ignore[no-untyped-def]
        return customer_id

    def open_order(customer_id):  # type: ignore[no-untyped-def]
        return customer_id

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "open_customer": open_customer,
            "open_order": open_order,
        },
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "open_customer: (customer_id)  # disambiguation: use open_customer" in locals_text
    assert "open_order: (customer_id)  # disambiguation: use open_order" in locals_text


def test_object_surface_limits_methods_and_fields() -> None:
    class Surface:
        f1 = 1
        f2 = 2
        f3 = 3

        def a(self) -> None:
            return None

        def b(self) -> None:
            return None

        def c(self) -> None:
            return None

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"surface": Surface()},
    )

    configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=8_000,
            locals_max_items=80,
            globals_max_tokens=4_000,
            globals_max_items=40,
            value_max_tokens=200,
            object_max_methods=2,
            object_max_fields=2,
            object_field_value_max_tokens=120,
            tool_result_max_tokens=1_200,
        )
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect surface.",
        step_context=step_context,
        configuration=configuration,
    )

    locals_text = locals_section(prompt)
    assert "surface.<methods>: <snipped 1 public methods>" in locals_text
    assert "surface.<fields>: <snipped 1 public fields>" in locals_text


def test_locals_section_renders_pep695_type_alias_in_function_signature() -> None:
    type T = Literal["A", "B", "C"]  # pyright: ignore[reportGeneralTypeIssues]

    def f(t: T) -> None:
        _ = t

    def g(literal_value: Literal["A", "B", "C"]) -> None:
        _ = literal_value

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"T": T, "f": f, "g": g},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "T: type = typing.Literal['A', 'B', 'C']" in locals_text
    assert "f: (t: T) -> None" in locals_text
    assert "g: (literal_value: Literal['A', 'B', 'C']) -> None" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_globals_section_includes_type_alias_from_local_callable_return_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check": check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check the text.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text

    locals_text = locals_section(prompt)
    assert "check: (text: str) -> Labels" in locals_text


def test_globals_section_includes_type_alias_from_local_callable_parameter_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def process(labels: Labels) -> str:
        _ = labels
        return ""

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"process": process},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Process.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text


def test_globals_section_includes_type_alias_from_generic_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def batch_check(texts: list[str]) -> list[Labels]:
        _ = texts
        return []

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"batch_check": batch_check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Batch check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text


def test_globals_section_includes_type_alias_from_nested_generic_annotation() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def deep_check(data: list[dict[str, Labels]]) -> None:
        _ = data

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"deep_check": deep_check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Deep check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text


def test_globals_section_includes_type_alias_from_global_callable_signature() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def classify(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels, "classify": classify},
        python_locals={},
        input_binding_names=["classify"],
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Classify the email by <classify> function.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in globals_text
    assert "classify: (text: str) -> Labels" in globals_text


def test_globals_section_excludes_builtin_types_from_callable_signature() -> None:
    def plain(text: str, count: int) -> list[str]:
        _ = text, count
        return []

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"plain": plain},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Run plain.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert globals_text.strip() == ""


def test_type_alias_in_locals_is_not_duplicated_in_globals_section() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check": check, "Labels": Labels},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels" not in globals_text

    locals_text = locals_section(prompt)
    assert "Labels: type = typing.Literal['good', 'bad']" in locals_text


def test_globals_section_deduplicates_type_alias_from_multiple_callables() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check_a(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    def check_b(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check_a": check_a, "check_b": check_b},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check both.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert globals_text.count("Labels:") == 1


def test_type_alias_in_globals_unreferenced_by_signature_is_not_discovered() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> str:
        _ = text
        return ""

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "Labels": Labels},
        python_locals={"check": check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels" not in globals_text


def test_type_alias_not_in_globals_is_not_discovered() -> None:
    type Labels = Literal["good", "bad"]  # pyright: ignore[reportGeneralTypeIssues]

    def check(text: str) -> Labels:  # pyright: ignore[reportReturnType]
        _ = text

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"check": check},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Check.",
        step_context=step_context,
    )

    globals_text = globals_section(prompt)
    assert "Labels" not in globals_text


def test_locals_section_renders_fallback_composed_function_signature() -> None:
    def primary(x: str) -> str:
        return f"primary:{x}"

    def backup(x: str) -> str:
        return f"backup:{x}"

    composed = fallback(primary, backup)

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"composed": composed},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "composed: (x: str) -> str" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_locals_section_renders_fallback_merged_return_type_signature() -> None:
    def primary(x: str) -> str:
        return f"primary:{x}"

    def backup(x: str) -> int:
        return len(f"backup:{x}")

    composed = fallback(primary, backup)

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"composed": composed},
    )

    prompt = build_user_prompt_text(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    locals_text = locals_section(prompt)
    assert "composed: (x: str) -> str | int" in locals_text
    assert "# " not in locals_text
    assert "<function>" not in locals_text


def test_build_user_prompt_returns_tuple_for_text_only_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"text": "hello"},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect locals.",
        step_context=step_context,
    )

    assert isinstance(prompt_content, tuple)
    assert all(isinstance(content, str) for content in prompt_content)
    assert prompt_content_to_text(prompt_content).startswith("<<<NH:PROGRAM>>>")


def test_locals_section_inlines_binary_content_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("photo: BinaryContent = ")
    content_after_photo = prompt_content[photo_index + 1]
    assert isinstance(content_after_photo, str)
    assert content_after_photo.startswith("\n<<<NH:END_LOCALS>>>")
    assert "photo: BinaryContent = <image>" in prompt_content_to_text(prompt_content)


def test_globals_section_inlines_image_url_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "photo": ImageUrl(url="https://example.com/cat.png")},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, ImageUrl))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("photo: ImageUrl = ")
    assert prompt_content_to_text(prompt_content).endswith("photo: ImageUrl = <image>\n<<<NH:END_GLOBALS>>>\n")


def test_locals_section_inlines_uploaded_file_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"photo": UploadedFile(file_id="file-123", provider_name="openai", media_type="image/png")},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, UploadedFile))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("photo: UploadedFile = ")
    assert "photo: UploadedFile = <image>" in prompt_content_to_text(prompt_content)


def test_locals_section_inlines_audio_url_as_file_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"clip": AudioUrl(url="https://example.com/sample.mp3", media_type="audio/mpeg")},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <clip>.",
        step_context=step_context,
    )

    clip_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, AudioUrl))
    content_before_clip = prompt_content[clip_index - 1]
    assert isinstance(content_before_clip, str)
    assert content_before_clip.endswith("clip: AudioUrl = ")
    assert "clip: AudioUrl = <file>" in prompt_content_to_text(prompt_content)


def test_locals_section_keeps_unreferenced_top_level_multimodal_bindings_by_design() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "extra": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
            "photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
        },
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <photo>.",
        step_context=step_context,
    )

    assert sum(1 for content in prompt_content if isinstance(content, BinaryContent)) == 2
    prompt_text = prompt_content_to_text(prompt_content)
    assert "extra: BinaryContent = <image>" in prompt_text
    assert "photo: BinaryContent = <image>" in prompt_text


def test_locals_section_inlines_top_level_user_content_sequence_for_provider_prompt() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"gallery": ["prefix ", BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"), " suffix"]},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <gallery>.",
        step_context=step_context,
    )

    image_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_image = prompt_content[image_index - 1]
    assert isinstance(content_before_image, str)
    assert content_before_image.endswith("gallery: list = prefix ")
    content_after_image = prompt_content[image_index + 1]
    assert isinstance(content_after_image, str)
    assert content_after_image.startswith(" suffix\n<<<NH:END_LOCALS>>>")
    assert "gallery: list = prefix <image> suffix" in prompt_content_to_text(prompt_content)


def test_top_level_text_only_sequence_remains_preview_only() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"lines": ["alpha", "beta"]},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <lines>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent | AudioUrl | ImageUrl | UploadedFile) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert 'lines: list = ["alpha","beta"]' in prompt_text


def test_dotted_multimodal_reference_adds_explicit_native_prompt_line() -> None:
    @dataclass
    class Holder:
        photo: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"))},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("holder.photo: BinaryContent = ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder: object = Holder" in prompt_text
    assert "holder.photo: BinaryContent = <image>" in prompt_text


def test_dotted_multimodal_reference_preserves_base_model_leaf_identity() -> None:
    class Holder(BaseModel):
        photo: object

        def describe(self) -> str:
            return "photo holder"

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"))},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("holder.photo: BinaryContent = ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.describe:" in prompt_text
    assert "holder.model_config:" not in prompt_text
    assert "holder.model_dump:" not in prompt_text
    assert "holder.model_fields:" not in prompt_text
    assert "holder.model_validate:" not in prompt_text
    assert "holder.photo: BinaryContent = <image>" in prompt_text


def test_dotted_non_multimodal_reference_remains_preview_only() -> None:
    @dataclass
    class Holder:
        count: int

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(count=3)},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.count>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.count: int = 3" in prompt_text


def test_dotted_multimodal_sequence_reference_is_hoisted() -> None:
    @dataclass
    class Holder:
        photos: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photos=[BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")])},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photos>.",
        step_context=step_context,
    )

    image_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_image = prompt_content[image_index - 1]
    assert isinstance(content_before_image, str)
    assert content_before_image.endswith("holder.photos: list = ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.photos: list = <image>" in prompt_text


def test_dotted_multimodal_sequence_reference_is_hoisted_from_globals() -> None:
    @dataclass
    class Holder:
        photos: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(photos=[BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")])},
        python_locals={"message": "hello"},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photos> and <message>.",
        step_context=step_context,
    )

    image_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_image = prompt_content[image_index - 1]
    assert isinstance(content_before_image, str)
    assert content_before_image.endswith("holder.photos: list = ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.photos: list = <image>" in prompt_text


def test_dotted_mapping_key_multimodal_reference_is_not_hoisted() -> None:
    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "payload": {"photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")},
        },
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <payload.photo>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "payload: dict = " in prompt_text
    assert "payload.photo: BinaryContent = <image>" not in prompt_text
    assert '"kind":"binary"' in prompt_text


def test_top_level_mixed_non_user_content_sequence_remains_preview_only() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "gallery": ["prefix ", {"label": "cat"}, BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")],
        },
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <gallery>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "gallery: list = [" in prompt_text
    assert '"label":"cat"' in prompt_text
    assert "<nonserializable>" in prompt_text


def test_namedtuple_multimodal_reference_remains_record_preview_only() -> None:
    PhotoReport = namedtuple("PhotoReport", ["caption", "photo"])
    report = PhotoReport(
        caption="cat",
        photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
    )

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={"report": report},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <report>.",
        step_context=step_context,
    )

    assert all(not isinstance(content, BinaryContent) for content in prompt_content)
    prompt_text = prompt_content_to_text(prompt_content)
    assert "report: PhotoReport = " in prompt_text
    assert '"caption":"cat"' in prompt_text
    assert '"kind":"binary"' in prompt_text


def test_deep_dotted_multimodal_reference_adds_explicit_native_prompt_line() -> None:
    @dataclass
    class Inner:
        photo: object

    @dataclass
    class Holder:
        inner: Inner

    step_context = build_step_context(
        python_globals={"__builtins__": builtins, "holder": Holder(inner=Inner(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")))},
        python_locals={},
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.inner.photo>.",
        step_context=step_context,
    )

    photo_index = next(index for index, content in enumerate(prompt_content) if isinstance(content, BinaryContent))
    content_before_photo = prompt_content[photo_index - 1]
    assert isinstance(content_before_photo, str)
    assert content_before_photo.endswith("holder.inner.photo: BinaryContent = ")
    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.inner:" in prompt_text
    assert "holder.inner.photo: BinaryContent = <image>" in prompt_text


def test_depth_one_dotted_multimodal_reference_survives_object_max_fields_truncation() -> None:
    @dataclass
    class Holder:
        alpha: int
        beta: int
        photo: object

    step_context = build_step_context(
        python_globals={
            "__builtins__": builtins,
            "holder": Holder(alpha=1, beta=2, photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")),
        },
        python_locals={},
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(object_max_fields=1),
    )

    prompt_content = build_user_prompt_content(
        processed_natural_program="Inspect <holder.photo>.",
        step_context=step_context,
        configuration=tight_configuration,
    )

    prompt_text = prompt_content_to_text(prompt_content)
    assert "holder.photo: BinaryContent = <image>" in prompt_text
    assert sum(1 for content in prompt_content if isinstance(content, BinaryContent)) == 1


def test_explicit_dotted_multimodal_reference_can_be_truncated_by_section_limits() -> None:
    @dataclass
    class Holder:
        photo: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "alpha": "hello",
            "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")),
        },
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_items=1,
            object_max_fields=0,
        ),
    )

    prompt_text = build_user_prompt_text(
        processed_natural_program="Inspect <holder.photo> and <alpha>.",
        step_context=step_context,
        configuration=tight_configuration,
    )

    assert "alpha: str = " in prompt_text
    assert "holder.photo: BinaryContent = <image>" not in prompt_text
    assert "<snipped>" in locals_section(prompt_text)


def test_token_truncation_log_marks_dropped_multimodal(caplog) -> None:  # type: ignore[no-untyped-def]
    @dataclass
    class Holder:
        photo: object

    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "alpha": "hello",
            "holder": Holder(photo=BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png")),
        },
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(
            locals_max_tokens=20,
            locals_max_items=80,
            object_max_fields=0,
        ),
    )

    with caplog.at_level("INFO", logger="nighthawk"):
        prompt_text = build_user_prompt_text(
            processed_natural_program="Inspect <holder.photo> and <alpha>.",
            step_context=step_context,
            configuration=tight_configuration,
        )

    assert "alpha: str = " in prompt_text
    assert "holder.photo: BinaryContent = <image>" not in prompt_text
    assert "<snipped>" in locals_section(prompt_text)

    truncation_log_record_list = [record for record in caplog.records if "prompt_context_truncated" in record.getMessage()]
    assert len(truncation_log_record_list) == 1
    message = truncation_log_record_list[0].getMessage()
    assert "'nighthawk.prompt_context.dropped_multimodal': True" in message


def test_multimodal_binding_charges_internal_token_budget() -> None:
    step_context = build_step_context(
        python_globals={"__builtins__": builtins},
        python_locals={
            "photo": BinaryContent(data=_VALID_PNG_HEADER, media_type="image/png"),
            "text_value": "hello",
        },
    )

    tight_configuration = nh.StepExecutorConfiguration(
        context_limits=nh.StepContextLimits(locals_max_tokens=10),
    )

    prompt_text = build_user_prompt_text(
        processed_natural_program="Inspect <photo> and <text_value>.",
        step_context=step_context,
        configuration=tight_configuration,
    )

    assert "<snipped>" in locals_section(prompt_text)


def test_coalesce_prompt_content_merges_text_content() -> None:
    from nighthawk.runtime._user_content import coalesce_user_content

    result = coalesce_user_content(["hello ", TextContent(content="world")])
    assert result == ("hello world",)


def test_count_prompt_line_tokens_handles_text_content() -> None:
    import tiktoken

    from nighthawk.runtime.prompt import _count_prompt_line_tokens

    encoding = tiktoken.get_encoding("o200k_base")
    tokens = _count_prompt_line_tokens(
        prompt_line=(TextContent(content="hello world"),),
        token_encoding=encoding,
    )
    assert tokens < 10
